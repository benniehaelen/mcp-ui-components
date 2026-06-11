import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Bundles index.html + the App SDK + our code into ONE self-contained
// dist/viewer.html, which the Python MCP server serves as the
// ui://lineage/viewer.html resource (mimeType text/html;profile=mcp-app).
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    minify: true,
    cssMinify: true,
    rollupOptions: { input: "index.html" },
    outDir: "dist",
    emptyOutDir: true,
  },
});
