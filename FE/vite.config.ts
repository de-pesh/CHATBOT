import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { federation } from "@module-federation/vite";

export default defineConfig({
  // The repo-root .env - the one config file - instead of a FE/.env of its own.
  // Only VITE_* variables are put into the browser bundle; nothing else in that
  // file (keys, connection strings) is ever exposed. A missing file is fine, as
  // in the Docker build, where the value arrives as a build argument instead.
  envDir: "..",
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
    minify: false,
    cssCodeSplit: false,
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
