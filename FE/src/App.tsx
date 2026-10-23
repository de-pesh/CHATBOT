import { CssBaseline } from "@mui/material";
import { useState } from "react";
import Chatbot from "./components/Chatbot";
import { Box } from "./remote-ui-stubs";

function App({ API_ROUTES, authToken }: { API_ROUTES?: any; authToken?: string }) {
  const [messages, setMessages] = useState<
    { sender: "user" | "assistant"; text: string }[]
  >([]);

  return (
    <>
      <CssBaseline />
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          height: "100%",
          minHeight: "100%",
          width: "100%",
        }}
      >
        <Chatbot
          messages={messages}
          setMessages={setMessages}
          hideCloseButton
          API_ROUTES={API_ROUTES}
          authToken={authToken}
        />
      </Box>
    </>
  );
}

export default App;
