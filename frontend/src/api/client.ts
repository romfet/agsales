import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Empty base => same-origin /api (dev: Vite proxy; prod: nginx).
const baseUrl = import.meta.env.VITE_API_BASE ?? "";

export const api = createClient<paths>({ baseUrl });

// Placeholder bearer auth, mirrors backend API_AUTH_TOKEN. Real SSO replaces this.
const token = import.meta.env.VITE_API_TOKEN;
if (token) {
  api.use({
    onRequest({ request }) {
      request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  });
}
