// Shared helpers: API constants, storage, error parsing.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export function stored(key) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : null;
  } catch {
    return null;
  }
}

export function save(key, value) {
  if (value == null) localStorage.removeItem(key);
  else localStorage.setItem(key, JSON.stringify(value));
}

export function projectStorageKey(projectId, key) {
  const scope = projectId ? encodeURIComponent(projectId) : "unscoped";
  return `aig.project.${scope}.${key}`;
}

export function pathFor(projectId, suffix = "") {
  return `/api/v1/projects/${encodeURIComponent(projectId)}${suffix}`;
}

export function projectDisplayName(manifest = {}, projectId = "") {
  const raw = String(manifest.agent_name || projectId || "Project").trim();
  const token = raw.match(/^[A-Za-z0-9][A-Za-z0-9_-]*/)?.[0];
  return token || raw;
}

export function projectPurpose(manifest = {}, projectId = "") {
  const raw = String(manifest.purpose || "").trim().replace(/\s+/g, " ");
  const englishWords = raw.match(/[A-Za-z]{3,}/g) || [];
  if (!raw || raw.length > 72 || englishWords.length >= 4) {
    return `当前已加载 ${projectDisplayName(manifest, projectId)} 项目，可查看版本、能力变化与评估结果。`;
  }
  return raw;
}

export function collection(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.items)) return value.items;
  if (Array.isArray(value?.results)) return value.results;
  return value && typeof value === "object" ? [value] : [];
}

export function getError(body, status) {
  if (typeof body?.detail === "string") return body.detail;
  if (body?.detail?.message) return body.detail.message;
  if (Array.isArray(body?.detail)) {
    return body.detail
      .map((item) => `${(item.loc || []).slice(-1)[0] || "request"}: ${item.msg || "invalid value"}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}
