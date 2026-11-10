import { defineConfig } from "vite";

export default defineConfig({
  base: "/static/world/",
  build: { outDir: "../static/world", emptyOutDir: true },
});
