import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { useState } from 'react';

export function useAxiosApi<T = any>() {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const request = async (config: AxiosRequestConfig) => {
    setLoading(true);
    setError(null);

    try {
      const response = await axios.request<T>(config);
      setData(response.data);
      return response.data;
    } catch (err: any) {
      setError(err?.message ?? 'Request failed');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return { data, error, loading, request };
}
