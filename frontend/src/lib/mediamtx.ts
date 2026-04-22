interface MediaMTXWhepClientOptions {
  url: string
  onTrack?: (event: RTCTrackEvent) => void
  onError?: (message: string) => void
}

interface ParsedOfferData {
  iceUfrag: string
  icePwd: string
  medias: string[]
}

function decodeQuotedCredential(value: string): string {
  return JSON.parse(`"${value}"`) as string
}

function parseIceServers(linkHeader: string | null): RTCIceServer[] {
  if (!linkHeader) return []

  return linkHeader
    .split(', ')
    .map((item) => {
      const match = item.match(/^<(.+?)>; rel="ice-server"(; username="(.*?)"; credential="(.*?)"; credential-type="password")?$/i)
      if (!match) return null

      const server: RTCIceServer = {
        urls: [match[1]],
      }

      if (match[3] && match[4]) {
        server.username = decodeQuotedCredential(match[3])
        server.credential = decodeQuotedCredential(match[4])
      }

      return server
    })
    .filter((item): item is RTCIceServer => Boolean(item))
}

function parseOfferData(sdp: string): ParsedOfferData {
  const parsed: ParsedOfferData = {
    iceUfrag: '',
    icePwd: '',
    medias: [],
  }

  sdp.split('\r\n').forEach((line) => {
    if (line.startsWith('m=')) {
      parsed.medias.push(line.slice(2))
      return
    }

    if (!parsed.iceUfrag && line.startsWith('a=ice-ufrag:')) {
      parsed.iceUfrag = line.slice('a=ice-ufrag:'.length)
      return
    }

    if (!parsed.icePwd && line.startsWith('a=ice-pwd:')) {
      parsed.icePwd = line.slice('a=ice-pwd:'.length)
    }
  })

  return parsed
}

function generateSdpFragment(offerData: ParsedOfferData, candidates: RTCIceCandidate[]): string {
  const grouped = new Map<number, RTCIceCandidate[]>()

  candidates.forEach((candidate) => {
    const mediaIndex = candidate.sdpMLineIndex
    if (mediaIndex == null) return

    const existing = grouped.get(mediaIndex) || []
    existing.push(candidate)
    grouped.set(mediaIndex, existing)
  })

  let fragment = `a=ice-ufrag:${offerData.iceUfrag}\r\na=ice-pwd:${offerData.icePwd}\r\n`

  offerData.medias.forEach((media, index) => {
    const mediaCandidates = grouped.get(index)
    if (!mediaCandidates || mediaCandidates.length === 0) return

    fragment += `m=${media}\r\na=mid:${index}\r\n`
    mediaCandidates.forEach((candidate) => {
      fragment += `a=${candidate.candidate}\r\n`
    })
  })

  return fragment
}

function extractResponseError(status: number, payload: unknown): string {
  const body = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  const responseError = typeof body.error === 'string' ? body.error : ''

  if (status === 404) return '找不到对应实时流，请确认流名称已发布'
  if (responseError) return responseError
  return `实时流握手失败 (${status})`
}

function resolveSessionUrl(locationHeader: string | null, baseUrl: string): string {
  if (!locationHeader) {
    throw new Error('实时流服务没有返回会话地址')
  }

  const absoluteBaseUrl = new URL(baseUrl, window.location.origin)
  const firstSegment = absoluteBaseUrl.pathname.split('/').filter(Boolean)[0]
  const proxiedPrefix = firstSegment ? `/${firstSegment}` : ''
  const resolvedUrl = new URL(locationHeader, absoluteBaseUrl)

  if (proxiedPrefix && !resolvedUrl.pathname.startsWith(`${proxiedPrefix}/`)) {
    return `${window.location.origin}${proxiedPrefix}${resolvedUrl.pathname}${resolvedUrl.search}${resolvedUrl.hash}`
  }

  return resolvedUrl.toString()
}

export class MediaMTXWhepClient {
  private readonly url: string
  private readonly onTrack?: (event: RTCTrackEvent) => void
  private readonly onError?: (message: string) => void
  private pc: RTCPeerConnection | null = null
  private sessionUrl: string | null = null
  private offerData: ParsedOfferData | null = null
  private queuedCandidates: RTCIceCandidate[] = []
  private closed = false

  constructor(options: MediaMTXWhepClientOptions) {
    this.url = options.url
    this.onTrack = options.onTrack
    this.onError = options.onError
  }

  async start() {
    const iceServers = await this.requestIceServers()
    if (this.closed) return

    const pc = new RTCPeerConnection({ iceServers })
    this.pc = pc

    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })
    pc.ontrack = (event) => this.onTrack?.(event)
    pc.onicecandidate = (event) => {
      void this.handleLocalCandidate(event)
    }
    pc.onconnectionstatechange = () => {
      const connectionState = pc.connectionState
      if (this.closed) return

      if (connectionState === 'failed' || connectionState === 'disconnected' || connectionState === 'closed') {
        this.handleError(connectionState === 'disconnected' ? '视频流连接断开，请重试' : '视频流连接失败，请检查 MediaMTX 和流发布状态')
      }
    }

    const offer = await pc.createOffer()
    if (!offer.sdp) {
      throw new Error('未生成可用的 WebRTC offer')
    }

    await pc.setLocalDescription(offer)
    this.offerData = parseOfferData(offer.sdp)

    const answerSdp = await this.sendOffer(offer.sdp)
    if (this.closed) return

    await pc.setRemoteDescription(new RTCSessionDescription({
      type: 'answer',
      sdp: answerSdp,
    }))

    if (this.closed || this.queuedCandidates.length === 0) return

    const pendingCandidates = [...this.queuedCandidates]
    this.queuedCandidates = []
    await this.sendLocalCandidates(pendingCandidates)
  }

  close() {
    if (this.closed) return
    this.closed = true

    const sessionUrl = this.sessionUrl
    this.sessionUrl = null
    this.queuedCandidates = []
    this.offerData = null

    if (this.pc) {
      this.pc.ontrack = null
      this.pc.onicecandidate = null
      this.pc.onconnectionstatechange = null
      this.pc.close()
      this.pc = null
    }

    if (sessionUrl) {
      void fetch(sessionUrl, { method: 'DELETE' }).catch(() => {})
    }
  }

  private handleError(message: string) {
    if (this.closed) return
    this.onError?.(message)
    this.close()
  }

  private async requestIceServers(): Promise<RTCIceServer[]> {
    try {
      const response = await fetch(this.url, { method: 'OPTIONS' })
      return parseIceServers(response.headers.get('Link'))
    } catch {
      return []
    }
  }

  private async sendOffer(offer: string): Promise<string> {
    const response = await fetch(this.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/sdp',
      },
      body: offer,
    })

    if (response.status !== 201) {
      let payload: unknown = null

      try {
        payload = await response.json()
      } catch {
        payload = null
      }

      throw new Error(extractResponseError(response.status, payload))
    }

    this.sessionUrl = resolveSessionUrl(response.headers.get('Location'), this.url)
    return response.text()
  }

  private async handleLocalCandidate(event: RTCPeerConnectionIceEvent) {
    if (this.closed || !event.candidate) return

    if (!this.sessionUrl) {
      this.queuedCandidates.push(event.candidate)
      return
    }

    try {
      await this.sendLocalCandidates([event.candidate])
    } catch (error) {
      this.handleError(error instanceof Error ? error.message : '候选地址上报失败')
    }
  }

  private async sendLocalCandidates(candidates: RTCIceCandidate[]) {
    if (this.closed || !this.sessionUrl || !this.offerData || candidates.length === 0) return

    const response = await fetch(this.sessionUrl, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/trickle-ice-sdpfrag',
        'If-Match': '*',
      },
      body: generateSdpFragment(this.offerData, candidates),
    })

    if (response.status === 204) return
    throw new Error(extractResponseError(response.status, null))
  }
}
