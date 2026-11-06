// One place for HTTP. Every request goes through getJson().

export class ApiError extends Error {}

export async function getJson<T>(
  path: string,
  params?: Record<string, string | number>,
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(`${path} returned ${res.status}`);
  }
  return (await res.json()) as T;
}
