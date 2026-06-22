import { defineConfig } from 'astro/config';

// Pure static site — Beds24 handles all booking/payment via embedded iframe.
// BASE: '/cocon' for temporary sub-path hosting at rental.changit.fr/cocon/.
// Set to '/' (or remove) once the site moves to its own domain (cocon-grenoble.fr).
export default defineConfig({
  site: 'https://cocon-grenoble.fr',
  base: process.env.SITE_BASE ?? '/cocon',
  trailingSlash: 'always',
  build: { format: 'directory' },
});
