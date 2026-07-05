// Thin fetch client for the backend API (spec section 23). Production/Docker use
// same-origin relative paths. Local Vite dev defaults to the FastAPI origin so
// the app still works if the dev-server proxy is stale or not loaded.

import type {
  Comparison,
  CompanyChecklist,
  CreateRunRequest,
  CreateRunResponse,
  CreateReviewResponse,
  IterationsReport,
  Memo,
  PageResponse,
  ReviewCard,
  ReviewReport,
  ReviewStatusResponse,
  RunCard,
  RunStatusResponse,
} from "./types";

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.DEV ? "http://localhost:8000" : "")
).replace(/\/$/, "");

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

async function parseJsonResponse<T>(res: Response, url: string): Promise<T> {
  const contentType = res.headers.get("content-type") || "";
  const text = await res.text();
  if (!text) {
    throw new ApiError(res.status, `Empty response from ${url}`);
  }
  try {
    return JSON.parse(text) as T;
  } catch (exc) {
    const hint =
      res.ok && !contentType.includes("application/json")
        ? " If you are running the Vite dev server, restart it so new API proxy routes are active."
        : "";
    throw new ApiError(
      res.status,
      `Expected JSON from ${url}, got ${contentType || "an untyped response"}.${hint}`,
    );
  }
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await parseJsonResponse<{ detail?: string }>(res, url);
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return parseJsonResponse<T>(res, url);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function listCompanies(): Promise<CompanyChecklist[]> {
  return getJson<CompanyChecklist[]>(apiUrl("/companies"));
}

export function listRuns(): Promise<RunCard[]> {
  return getJson<RunCard[]>(apiUrl("/runs"));
}

export function getRun(runId: string): Promise<RunStatusResponse> {
  return getJson<RunStatusResponse>(apiUrl(`/runs/${encodeURIComponent(runId)}`));
}

export async function createRun(req: CreateRunRequest): Promise<CreateRunResponse> {
  const res = await fetch(apiUrl("/runs"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText);
  }
  return res.json() as Promise<CreateRunResponse>;
}

/** 200 memo | 202 running (not ready) | 404 missing | 500 failed. Never throws for 202/404/500 --
 * callers get a discriminated result instead so the UI can show "running"/"missing"/"failed" states. */
export type MemoResult =
  | { kind: "ready"; memo: Memo }
  | { kind: "pending" }
  | { kind: "missing" }
  | { kind: "failed"; error: string };

export async function getMemo(runId: string): Promise<MemoResult> {
  const res = await fetch(apiUrl(`/runs/${encodeURIComponent(runId)}/memo`));
  const body = await res.json().catch(() => ({}));
  if (res.status === 200) return { kind: "ready", memo: body as Memo };
  if (res.status === 202) return { kind: "pending" };
  if (res.status === 404) return { kind: "missing" };
  return { kind: "failed", error: body.error ?? "run failed" };
}

export function getPage(company: string, docId: string, page: number): Promise<PageResponse> {
  return getJson<PageResponse>(
    apiUrl(`/corpus/${encodeURIComponent(company)}/${encodeURIComponent(docId)}/page/${page}`),
  );
}

export function getEvalResults(): Promise<Comparison> {
  return getJson<Comparison>(apiUrl("/evals/results"));
}

/** Cumulative improvement-loop dataset (baseline61 + iter1..iter5), all rescored
 * under the final scorer. 404 when the analysis pipeline hasn't built it yet. */
export function getEvalIterations(): Promise<IterationsReport> {
  return getJson<IterationsReport>(apiUrl("/evals/iterations"));
}

export function listReviews(): Promise<ReviewCard[]> {
  return getJson<ReviewCard[]>(apiUrl("/reviews"));
}

export function getReview(reviewId: string): Promise<ReviewStatusResponse> {
  return getJson<ReviewStatusResponse>(apiUrl(`/reviews/${encodeURIComponent(reviewId)}`));
}

export async function createReview(file: File, pilot = true): Promise<CreateReviewResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("pilot", String(pilot));
  const res = await fetch(apiUrl("/reviews"), {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<CreateReviewResponse>;
}

export async function runFullReview(reviewId: string): Promise<CreateReviewResponse | ReviewStatusResponse> {
  const res = await fetch(apiUrl(`/reviews/${encodeURIComponent(reviewId)}/full`), {
    method: "POST",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<CreateReviewResponse | ReviewStatusResponse>;
}

export function getReviewReport(reviewId: string): Promise<ReviewReport> {
  return getJson<ReviewReport>(apiUrl(`/reviews/${encodeURIComponent(reviewId)}/report`));
}

export async function getReviewReportHtml(reviewId: string): Promise<string> {
  const res = await fetch(apiUrl(`/reviews/${encodeURIComponent(reviewId)}/report?format=html`));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  return res.text();
}

export function annotatedReviewUrl(reviewId: string): string {
  return apiUrl(`/reviews/${encodeURIComponent(reviewId)}/annotated`);
}

/** GET /runs/{id}/events (SSE): live queue while running, replay-with-sleeps once
 * complete -- same EventSource code path either way (spec section 23). Returns a
 * cleanup function; call it to close the connection (e.g. on unmount / re-run). */
export function streamRunEvents<T>(
  runId: string,
  onEvent: (event: T) => void,
  onDone: () => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(apiUrl(`/runs/${encodeURIComponent(runId)}/events`));
  source.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as T);
    } catch {
      // malformed event; ignore rather than tear down the stream
    }
  };
  source.onerror = () => {
    // EventSource fires onerror when the server closes the stream (both live-end
    // and replay-end look like this to the browser) as well as on real network
    // errors; either way there is nothing more to read, so close and notify.
    source.close();
    onDone();
    onError?.();
  };
  return () => source.close();
}

/** GET /reviews/{id}/events (SSE): live review events while running, replay once complete. */
export function streamReviewEvents<T>(
  reviewId: string,
  onEvent: (event: T) => void,
  onDone: () => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(apiUrl(`/reviews/${encodeURIComponent(reviewId)}/events`));
  source.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data) as T);
    } catch {
      // malformed event; ignore rather than tear down the stream
    }
  };
  source.onerror = () => {
    source.close();
    onDone();
    onError?.();
  };
  return () => source.close();
}
