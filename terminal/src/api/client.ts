// One place for HTTP. Every request goes through getJson().

export class ApiError extends Error {}

function buildUrl(path: string, params?: Record<string, string | number>): URL {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      url.searchParams.set(key, String(value));
    }
  }
  return url;
}

export async function getJson<T>(
  path: string,
  params?: Record<string, string | number>,
): Promise<T> {
  const res = await fetch(buildUrl(path, params));
  if (!res.ok) {
    throw new ApiError(`${path} returned ${res.status}`);
  }
  return (await res.json()) as T;
}

// POST with the parameters on the query string, as the Flask routes read them.
export async function postJson<T>(
  path: string,
  params?: Record<string, string | number>,
): Promise<T> {
  const res = await fetch(buildUrl(path, params), { method: "POST" });
  if (!res.ok) {
    throw new ApiError(`${path} returned ${res.status}`);
  }
  return (await res.json()) as T;
}
