declare module "remote_ui_library/components" {
  export const Fifth: React.ComponentType<any>;
  export const Table: React.ComponentType<any>;
  export const Modal: React.ComponentType<any>;
  export const Accordion: React.ComponentType<any>;
  export const Tooltip: React.ComponentType<any>;
  export const Toast: React.ComponentType<any>;
  export const Loader: React.ComponentType<any>;
  export const ProgressBar: React.ComponentType<any>;
  export const Avatar: React.ComponentType<any>;
  export const Chip: React.ComponentType<any>;
  export const IconButton: React.ComponentType<any>;
  export const Button: React.ComponentType<any>;
  export const Stepper: React.ComponentType<any>;
  export const MultiSelect: React.ComponentType<any>;
  export const SingleSelect: React.ComponentType<any>;
  export const LocalButtons: React.ComponentType<any>;
  export const FrameworkCard: React.ComponentType<any>;
  export const AssesmentCard: React.ComponentType<any>;
  export const CheckboxGroup: React.ComponentType<any>;
  export const RadioGroup: React.ComponentType<any>;
  export const CheckBox: React.ComponentType<any>;
  export const TextField: React.ComponentType<any>;
  export const Select: React.ComponentType<any>;
  export const FileUpload: React.ComponentType<any>;
  export const AppBar: React.ComponentType<any>;
  export const Box: React.ComponentType<any>;
}

declare module "remote_ui_library/AppTheme" {
  export const appTheme: import("@mui/material").Theme;
}

declare module "remote_ui_library/hooks" {
  export function useAxiosApi<T = any>(): {
    data: T | null;
    error: string | null;
    loading: boolean;
    request: (config: any) => Promise<T | null>;
  };

  export function useFetchApi<T = any>(): {
    data: T | null;
    error: string | null;
    loading: boolean;
    request: (url: string, options?: RequestInit) => Promise<T | null>;
  };

  export const useApi: typeof useAxiosApi | typeof useFetchApi;
}
