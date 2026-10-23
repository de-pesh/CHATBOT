import { useState } from "react";
import { Typography } from "@mui/material";
import { Box, Button, TextField } from "../remote-ui-stubs";

interface AuthGateProps {
  onSubmit: (email: string, password: string) => void;
  error: string | null;
  loading: boolean;
}

/** Covers the chat area with a sign-in form. Shown only once the server has actually
 * rejected a request with 401 - see useAuthToken.authRequired. */
const AuthGate: React.FC<AuthGateProps> = ({ onSubmit, error, loading }) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password || loading) return;
    onSubmit(email, password);
  };

  return (
    <Box
      component="form"
      onSubmit={submit}
      sx={{
        position: "absolute",
        inset: 0,
        zIndex: 10,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1.5,
        px: 3,
        bgcolor: "rgba(255,255,255,0.98)",
      }}
    >
      <Typography variant="h6">Sign in</Typography>
      <Typography variant="body2" sx={{ color: "#5b6b82", textAlign: "center", maxWidth: 320 }}>
        Sign in to chat - this keeps your conversations separate from everyone else's.
      </Typography>
      <TextField
        label="Email"
        type="email"
        value={email}
        onChange={(e: any) => setEmail(e.target.value)}
        fullWidth
        autoFocus
        sx={{ maxWidth: 320 }}
      />
      <TextField
        label="Password"
        type="password"
        value={password}
        onChange={(e: any) => setPassword(e.target.value)}
        fullWidth
        sx={{ maxWidth: 320 }}
      />
      {error && (
        <Typography variant="body2" color="error">
          {error}
        </Typography>
      )}
      <Button type="submit" variant="contained" disabled={loading} sx={{ backgroundColor: "#4F81BD" }}>
        {loading ? "Signing in..." : "Sign in"}
      </Button>
    </Box>
  );
};

export default AuthGate;
