import type { Dispatch, SetStateAction } from 'react'

interface Props {
  stages: string[]
  setStages: Dispatch<SetStateAction<string[]>>
  onDirty: () => void
}

export default function MatchWindowEditor({ stages, setStages, onDirty }: Props) {
  return (
    <div className="mt-4 rounded-lg border border-border bg-secondary/30 p-3">
      <div className="text-sm font-medium">消费匹配轮次</div>
      <p className="mt-2 text-xs text-muted-foreground">
        同一天全部流水完成一轮后再扩大范围。同轮按校正后时差从小到大匹配，金额须完全一致。
      </p>
      <div className="mt-3 space-y-2">
        {stages.map((seconds, index) => (
          <div key={index} className="flex items-center gap-2">
            <label htmlFor={`match-round-${index}`} className="shrink-0 text-xs">第 {index + 1} 轮 ±</label>
            <input
              id={`match-round-${index}`}
              type="number" min={1} max={86400} step={1} value={seconds}
              onChange={(event) => {
                const value = event.target.value
                setStages((previous) => previous.map((item, i) => i === index ? value : item))
                onDirty()
              }}
              className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
            <span className="text-xs">秒</span>
            <button
              type="button" disabled={stages.length === 1}
              aria-label={`删除第 ${index + 1} 轮`}
              onClick={() => {
                setStages((previous) => previous.filter((_, i) => i !== index))
                onDirty()
              }}
              className="rounded px-2 py-2 text-xs text-muted-foreground hover:bg-background disabled:opacity-40"
            >删除</button>
          </div>
        ))}
      </div>
      <button
        type="button" disabled={Number(stages[stages.length - 1]) >= 86400}
        onClick={() => {
          setStages((previous) => [...previous, String(Math.min(86400, Number(previous[previous.length - 1] || 0) + 2))])
          onDirty()
        }}
        className="mt-3 rounded-lg border border-border bg-background px-3 py-2 text-xs hover:bg-secondary disabled:opacity-40"
      >添加轮次</button>
      <p className="mt-3 text-xs text-muted-foreground">
        共 {stages.length} 轮：{stages.map((seconds) => `±${seconds || '…'} 秒`).join(' → ')}。
        保存后下次匹配生效；历史日期可在消费匹配页重新匹配。人工匹配和已确认结果会保留。
      </p>
    </div>
  )
}
