import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Bundles an HTML entry + the App SDK + our code into ONE self-contained file
// under dist/, which the Python MCP server serves as a ui:// resource
// (mimeType text/html;profile=mcp-app). The entry is selected via the INPUT env
// var so the same config builds both widgets:
//   INPUT=index.html         -> dist/index.html        (lineage viewer)
//   INPUT=join-diagram.html  -> dist/join-diagram.html (join diagram)
const input = process.env.INPUT || "index.html";

export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    minify: true,
    cssMinify: true,
    rollupOptions: { input },
    outDir: "dist",
    emptyOutDir: true,
  },
});
