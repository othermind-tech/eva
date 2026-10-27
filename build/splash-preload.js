'use strict'

const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('splashBridge', {
  onStatusUpdate: (cb) => ipcRenderer.on('status-update', (_e, data) => cb(data)),
})
