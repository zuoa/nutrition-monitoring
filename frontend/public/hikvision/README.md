# Hikvision Web Plugin SDK

Put Hikvision WebComponentsKit direct-device SDK files here.

Required:

- `webVideoCtrl.js`

Install the matching Hikvision browser plugin on the preview workstation, then
refresh the admin page. The channel manager loads `/hikvision/webVideoCtrl.js`
at runtime and calls the plugin APIs directly:

- `I_InitPlugin`
- `I_InsertOBJECTPlugin`
- `I_Login`
- `I_StartRealPlay`

The `jsWebControl-1.0.0.min.js` SDK is for Hikvision platform preview flows
that use `appkey`, `secret`, and `cameraIndexCode`. This project currently
stores direct IPC/NVR credentials, so the channel manager uses
`webVideoCtrl.js` instead.
