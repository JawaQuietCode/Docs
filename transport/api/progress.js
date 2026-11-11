// progress.js — pembantu progress indicator (planning steps) Meta AI.
//
// Letakkan di berkas sendiri (bukan di airich.js) karena plugin memuat modul
// ini dengan `import` biasa, sementara transport sudah meng-cache airich.js
// versi lama. Berkas baru = cache baru = helper langsung terpakai tanpa
// restart transport.
//
// Struktur target (protobuf IBotProgressIndicatorMetadata):
//   botMetadata.progressIndicatorMetadata
//     ├─ progressDescription        : string
//     ├─ stepsMetadata[]            : IBotPlanningStepMetadata
//     │    ├─ statusTitle           : string
//     │    ├─ statusBody            : string?
//     │    ├─ status                : PLANNED | EXECUTING | FINISHED
//     │    ├─ isReasoning           : bool
//     │    ├─ isEnhancedSearch      : bool
//     │    ├─ sourcesMetadata[]     : judul + URL sumber
//     │    └─ sections[]            : sub-bagian (judul + isi)
//     └─ estimatedCompletionTime    : unix seconds

// PlanningStepStatus (nilai enum sama dengan protobuf).
export const PlanningStepStatus = {
  UNKNOWN: 0,
  PLANNED: 1,
  EXECUTING: 2,
  FINISHED: 3,
}

// Satu langkah. `status` boleh string ('PLANNED'|'EXECUTING'|'FINISHED') atau angka enum.
export function progressStep({ title, body, status = 'PLANNED', isReasoning = false, isEnhancedSearch = false, sources = [], sections = [] } = {}) {
  const statusMap = {
    UNKNOWN: PlanningStepStatus.UNKNOWN,
    PLANNED: PlanningStepStatus.PLANNED,
    EXECUTING: PlanningStepStatus.EXECUTING,
    FINISHED: PlanningStepStatus.FINISHED,
  }
  return {
    title,
    body,
    status: typeof status === 'string' ? (statusMap[status] ?? PlanningStepStatus.PLANNED) : status,
    isReasoning,
    isEnhancedSearch,
    sources,
    sections,
  }
}

/**
 * Susun objek progressIndicatorMetadata siap tempel ke botMetadata.
 * `estimatedCompletionTime` boleh ms atau detik (heuristik: > 1e12 = ms).
 */
export function buildProgressIndicatorMetadata({ description, steps = [], estimatedCompletionTime } = {}) {
  const stepMetas = steps.map((s, i) => ({
    statusTitle: String(s?.title ?? `Step ${i + 1}`),
    ...(s?.body ? { statusBody: String(s.body) } : {}),
    status: s?.status ?? PlanningStepStatus.PLANNED,
    isReasoning: Boolean(s?.isReasoning),
    isEnhancedSearch: Boolean(s?.isEnhancedSearch),
    sourcesMetadata: (s?.sources || []).map((src) => ({
      sourceTitle: String(src?.title ?? src?.url ?? ''),
      sourceUrl: String(src?.url ?? ''),
      provider: src?.provider ?? 1, // OTHER = 1
      favIconUrl: src?.favIconUrl ?? '',
    })),
    sections: (s?.sections || []).map((sec) => ({
      sectionTitle: String(sec?.title ?? ''),
      sectionBody: String(sec?.body ?? ''),
      sourcesMetadata: (sec?.sources || []).map((src) => ({
        title: String(src?.title ?? ''),
        provider: src?.provider ?? 1,
        sourceUrl: String(src?.url ?? ''),
        favIconUrl: src?.favIconUrl ?? '',
      })),
    })),
  }))

  let est = estimatedCompletionTime
  if (est && est > 1e12) est = Math.floor(est / 1000) // ms -> detik

  return {
    progressDescription: String(description ?? ''),
    stepsMetadata: stepMetas,
    ...(est ? { estimatedCompletionTime: Number(est) } : {}),
  }
}
