import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Not Vite's default 5173: my Transit Lab dev server uses it. strictPort fails loudly instead of
  // silently picking another port the API's CORS list doesn't know.
  server: { port: 5180, strictPort: true },
});
