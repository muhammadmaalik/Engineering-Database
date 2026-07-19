# Occhialini Website

Static product website for Occhialini, the public identity of the Motherbrain
engineering platform. No build step or dependencies required.

## Preview

From the repository root:

```powershell
python -m http.server 4173 --directory website
```

Then open `http://127.0.0.1:4173`.

## Deploy

Upload the contents of this directory to any static host (GitHub Pages, Netlify,
Cloudflare Pages, Vercel, or a standard web server).

Files:

- `index.html` — page structure and product content
- `styles.css` — responsive space/mission visual system
- `script.js` — ASCII moon, star field, scroll motion, reveals, and interactions
