// Base-aware URL helper so internal links work whether the site is served
// at the domain root ('/') or under a sub-path ('/cocon').
// import.meta.env.BASE_URL is e.g. '/cocon/' or '/'.
const base = import.meta.env.BASE_URL.replace(/\/$/, '');

/** Prefix an absolute app path with the configured base. Pure hash links pass through. */
export function url(path: string): string {
  if (path.startsWith('#')) return path;
  const clean = path.startsWith('/') ? path : `/${path}`;
  return `${base}${clean}`;
}
