/** Typed browser boundary for the existing localhost HTTP backend. */
export interface ApiEnvelope {
  ok?: boolean;
  error?: string | { message?: string; code?: string };
}

export type Method = 'GET' | 'POST';

export async function request<T extends ApiEnvelope>(
  path: string,
  method: Method = 'GET',
  payload?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  if (!path.startsWith('/api/')) {
    throw new Error('Only local ContentMachine API routes are allowed.');
  }
  const response = await fetch(path, {
    method,
    cache: 'no-store',
    signal,
    credentials: 'same-origin',
    ...(method === 'POST'
      ? {
          headers: {
            'Content-Type': 'application/json',
            'X-ContentMachine': '1',
          },
          body: JSON.stringify(payload ?? {}),
        }
      : {}),
  });
  const body: unknown = await response.json();
  if (!body || typeof body !== 'object') {
    throw new Error('Некорректный ответ сервера.');
  }
  const data = body as T;
  if (!response.ok || data.error) {
    const message = typeof data.error === 'string'
      ? data.error
      : data.error?.message || data.error?.code;
    throw new Error(message || 'HTTP ' + response.status);
  }
  return data;
}
