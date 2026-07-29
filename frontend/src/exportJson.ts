interface WritableFileHandle {
  createWritable(): Promise<{
    write(data: string): Promise<void>
    close(): Promise<void>
  }>
}

interface ExportDirectoryHandle {
  getFileHandle(name: string, options: { create: boolean }): Promise<WritableFileHandle>
}

interface DirectoryPickerWindow extends Window {
  showDirectoryPicker?: (options?: { mode?: 'read' | 'readwrite' }) => Promise<ExportDirectoryHandle>
}

function safeFileName(value: string): string {
  const normalized = value.trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[. ]+$/g, '')
  return (normalized || 'export').slice(0, 120)
}

export function exportEnvelope(format: string, data: unknown): Record<string, unknown> {
  return {
    format,
    version: 1,
    exported_at: new Date().toISOString(),
    data,
  }
}

export async function exportJsonToFolder(name: string, data: unknown): Promise<boolean> {
  const fileName = `${safeFileName(name)}.json`
  const content = `${JSON.stringify(data, null, 2)}\n`
  const pickerWindow = window as DirectoryPickerWindow
  if (pickerWindow.showDirectoryPicker) {
    try {
      const directory = await pickerWindow.showDirectoryPicker({ mode: 'readwrite' })
      const file = await directory.getFileHandle(fileName, { create: true })
      const writer = await file.createWritable()
      await writer.write(content)
      await writer.close()
      return true
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return false
      throw reason
    }
  }

  const blob = new Blob([content], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.click()
  URL.revokeObjectURL(url)
  return true
}
