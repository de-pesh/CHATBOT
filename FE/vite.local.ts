// Local Docker build. Identical to vite.qa.ts (same base "/chatbot/",
// which FE/nginx.conf serves) minus the Module Federation remote that
// vite.qa.ts fetches from a public IP - nothing under src/ imports it,
// and an unreachable remoteEntry.js would stall the standalone app.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { federation } from "@module-federation/vite";

export default defineConfig({
  base: "/chatbot/",
  plugins: [
    react(),
    federation({
      name: "remote_chatbot",
      filename: "remoteEntry.js",
      remotes: {},
      exposes: {
        "./Chatbot": "./src/App.tsx",
        "./FloatingChatbot": "./src/components/FloatingChat.tsx",
      },
      shared: {
        react: { singleton: true, requiredVersion: "^19.1.0" },
        "react-dom": { singleton: true, requiredVersion: "^19.1.0" },
        "react-router-dom": { singleton: true },
      },
    }),
  ],
  build: {
    target: "esnext",
    minify: true,
    cssCodeSplit: true,
  },
  server: {
    port: 3006, // match your dev server port
    proxy: {
      "/api": {
        // target:
        //   "https://eygenaistudio-apim-kubernetes-dev.azure-api.net/eygs-ctp/api",
        // target:"http://127.0.0.1:8000",
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        secure: false, // ignore SSL issues if using self-signed certs
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
    cors: {
      origin: "*",
      methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
      //allowedHeaders: ["Content-Type", "Authorization"],
      credentials: true,
    },
  },
  mode: "development",
});
