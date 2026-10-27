// Section 90 (V6.49 Phase 3) — background processes surfaced in the Activity
// panel. Rows themselves come from executionsApi.listRunning() (they're just
// RunningExecutionSummary rows with execution_mode: "background_process");
// this is only the one action the panel adds: stop the OS process directly.
import { patch, post } from './client'

export const processesApi = {
  stop: (processId: string) =>
    post<{ status: 'stopped' }>(`/processes/${processId}/stop`),
  // Section 96 (V6.58 Phase 2) — pins/unpins a running process to survive
  // an EVA quit. Pure DB flag; doesn't touch the OS process itself.
  setKeepAlive: (processId: string, keepAlive: boolean) =>
    patch<{ status: 'ok' }>(`/processes/${processId}/keep-alive`, { keep_alive_on_app_shutdown: keepAlive }),
}
