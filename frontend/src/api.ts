// Thin fetch client for the backend API (spec section 23). Same-origin relative
// paths -- the Vite dev server proxies these to :8000 (see vite.config.ts); a
// production build serves the frontend from the same origin as the API.

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

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
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
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function listCompanies(): Promise<CompanyChecklist[]> {
  return getJson<CompanyChecklist[]>("/companies");
}

export function listRuns(): Promise<RunCard[]> {
  return getJson<RunCard[]>("/runs");
}

export function getRun(runId: string): Promise<RunStatusResponse> {
  return getJson<RunStatusResponse>(`/runs/${encodeURIComponent(runId)}`);
}

export async function createRun(req: CreateRunRequest): Promise<CreateRunResponse> {
  const res = await fetch("/runs", {
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
  const res = await fetch(`/runs/${encodeURIComponent(runId)}/memo`);
  const body = await res.json().catch(() => ({}));
  if (res.status === 200) return { kind: "ready", memo: body as Memo };
  if (res.status === 202) return { kind: "pending" };
  if (res.status === 404) return { kind: "missing" };
  return { kind: "failed", error: body.error ?? "run failed" };
}

export function getPage(company: string, docId: string, page: number): Promise<PageResponse> {
  return getJson<PageResponse>(
    `/corpus/${encodeURIComponent(company)}/${encodeURIComponent(docId)}/page/${page}`,
  );
}

export function getEvalResults(): Promise<Comparison> {
  return getJson<Comparison>("/evals/results");
}

/** Cumulative improvement-loop dataset (baseline61 + iter1..iter5), all rescored
 * under the final scorer. 404 when the analysis pipeline hasn't built it yet. */
export function getEvalIterations(): Promise<IterationsReport> {
  return getJson<IterationsReport>("/evals/iterations");
}

export function listReviews(): Promise<ReviewCard[]> {
  return getJson<ReviewCard[]>("/reviews");
}

export function getReview(reviewId: string): Promise<ReviewStatusResponse> {
  return getJson<ReviewStatusResponse>(`/reviews/${encodeURIComponent(reviewId)}`);
}

export async function createReview(file: File, pilot = true): Promise<CreateReviewResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("pilot", String(pilot));
  const res = await fetch("/reviews", {
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
  const res = await fetch(`/reviews/${encodeURIComponent(reviewId)}/full`, {
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
  return getJson<ReviewReport>(`/reviews/${encodeURIComponent(reviewId)}/report`);
}

export async function getReviewReportHtml(reviewId: string): Promise<string> {
  const res = await fetch(`/reviews/${encodeURIComponent(reviewId)}/report?format=html`);
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
  return `/reviews/${encodeURIComponent(reviewId)}/annotated`;
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
  const source = new EventSource(`/runs/${encodeURIComponent(runId)}/events`);
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
  const source = new EventSource(`/reviews/${encodeURIComponent(reviewId)}/events`);
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
