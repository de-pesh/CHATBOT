import { useEffect, useState } from "react";
import { ClickAwayListener, Fab } from "@mui/material";
import Chatbot from "./Chatbot";
import { Box } from "../remote-ui-stubs";
import ChatIcon from "@mui/icons-material/Chat";

interface FloatChatProps {
  chatMode?: "closed" | "fab" | "window" | "fullscreen";
  onClose?: () => void;
  onOpenFromFab?: () => void;
  API_ROUTES?: any;
  currentPathname?: string;
  authToken?: string;
}

const FloatingChatbot: React.FC<FloatChatProps> = ({
  chatMode,
  onClose,
  onOpenFromFab,
  API_ROUTES,
  currentPathname,
  authToken,
}) => {
  const [messages, setMessages] = useState<
    { sender: "user" | "assistant"; text: string }[]
  >([]);
  const pathname = currentPathname ?? window.location.pathname;

  const isChatbotRoute = pathname.startsWith("/chatbot");
  const effectiveChatMode = chatMode ?? "fab";
  const showFab = !isChatbotRoute && effectiveChatMode === "fab";
  const showChat =
    effectiveChatMode === "window" || effectiveChatMode === "fullscreen";
  const isFullscreen = effectiveChatMode === "fullscreen";

  useEffect(() => {
    if (!showChat) {
      setMessages([]);
    }
  }, [showChat]);


  return (
    <>
      {/* FAB only when header-opened & minimized */}
      {showFab && (
        <Fab
          sx={{
            position: "fixed",
            bottom: 24,
            right: 24,
            zIndex: 1400,
            backgroundColor: "#4F81BD",
            color: "#fff",
            // height: "5vw",
            cursor: "pointer",
          }}
          onClick={onOpenFromFab}
        >
          <ChatIcon />
        </Fab>
      )}
      {showChat && (
        <ClickAwayListener onClickAway={() => { if (!isFullscreen) onClose?.(); }}>
          <Box
            sx={{
              position: isFullscreen ? "absolute" : "fixed",
              zIndex: 1400,
              bgcolor: "background.paper",
              boxShadow: isFullscreen ? 0 : 6,
              borderRadius: isFullscreen ? 0 : 2,
              overflow: "hidden",

              ...(isFullscreen
                ? {
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                }
                : {
                  bottom: 24,
                  right: 24,
                  width: 380,
                  height: "85vh",
                }),
              transition: "all 0.3s ease",
            }}
          >
            <Chatbot
              messages={messages}
              setMessages={setMessages}
              onClose={onClose}
              hideCloseButton={isFullscreen}
              API_ROUTES={API_ROUTES}
              authToken={authToken}
            />
          </Box>
        </ClickAwayListener>
      )}
    </>
  );
};

export default FloatingChatbot;
