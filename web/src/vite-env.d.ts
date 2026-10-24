/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MOCK?: string;
  readonly VITE_RECALL_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
