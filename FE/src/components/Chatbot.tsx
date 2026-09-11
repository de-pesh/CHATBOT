import React, { useState } from "react";
import {
  Toolbar,
  Typography,
  Box,
  Paper,
  Stack,
  InputAdornment,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import SmartToyOutlinedIcon from "@mui/icons-material/SmartToyOutlined";
import PersonOutlineOutlinedIcon from "@mui/icons-material/PersonOutlineOutlined";
import {
  Button,
  IconButton,
  TextField,
  AppBar,
  Accordion,
  Chip,
  Tooltip,
} from "../remote-ui-stubs";
import MapsUgcIcon from "@mui/icons-material/MapsUgc";
import HistoryIcon from "@mui/icons-material/History";
import MoreHorizIcon from "@mui/icons-material/MoreHoriz";
import SearchIcon from "@mui/icons-material/Search";
import { useAxiosApi } from "../hooks/useAxiosApi";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import Rating from "@mui/material/Rating";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import CloseIcon from "@mui/icons-material/Close";

const CHATBOT_ASSESSMENT_ID_KEY = "chatbotAssessmentId";

// Number of prior messages sent back as conversation context. Bounded so the
// rephrase prompt cannot grow without limit on long conversations.
const MAX_HISTORY_MESSAGES = 10;

const getStoredAssessmentId = () => {
  const storedValue = localStorage.getItem(CHATBOT_ASSESSMENT_ID_KEY);

  if (!storedValue) {
    return undefined;
  }

  const resolvedAssessmentId = Number(storedValue);

  return Number.isFinite(resolvedAssessmentId) && resolvedAssessmentId > 0
    ? resolvedAssessmentId
    : undefined;
};

interface ChatbotProps {
  messages: { sender: "user" | "assistant"; text: string }[];
  setMessages: React.Dispatch<React.SetStateAction<any[]>>;
  onClose?: () => void;
  hideCloseButton?: boolean;
  API_ROUTES?: any;
  assessmentId?: number;
}

const Chatbot: React.FC<ChatbotProps> = ({
  messages,
  setMessages,
  onClose,
  hideCloseButton,
  API_ROUTES,
  assessmentId,
}) => {
  // const [messages, setMessages] = useState<
  //   { sender: "user" | "assistant"; text: string }[]
  // >([]);

  console.log(API_ROUTES, "api routes in chatbot");
  
  const [getHistroy, setHistory] = useState<any[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [activeTab, setActiveTab] = useState<"chat" | "prompts" | "history">(
    "chat",
  );
  const [ratingValue, setRatingValue] = React.useState<number | null>(null);
  const allChips = ["Consulting", "Assurance", "Tax"]; // Your full list
  const maxVisible = 2; // Show only 2 chips
  const [searchTerm, setSearchTerm] = useState("");
  const userName = localStorage.getItem("user"); //|| "emmanullah.i@in.ey.com";

  // ✅ Switch client: "axios" | "fetch"
  //const { request, loading, error } = useApi<{ response: string }>("fetch");
  const { request, loading, error } = useAxiosApi();
  const visibleChips = allChips.slice(0, maxVisible);
  const hiddenCount = allChips.length - visibleChips.length;
  const handleSend = async () => {
    if (!input.trim()) return;

    const userMessage = { sender: "user" as const, text: input };
    // `messages` holds the transcript as it stood before this turn, so the new
    // question is appended explicitly rather than being read back from state.
    const history = messages.slice(-MAX_HISTORY_MESSAGES).map((message) => ({
      role: message.sender === "user" ? "user" : "assistant",
      content: message.text,
    }));
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    const resolvedAssessmentId = getStoredAssessmentId() ?? assessmentId;

    const payload = {
      conversation_id: conversationId,
      user_id: userName,
      chat: [...history, { role: "user", content: input }],
      ...(resolvedAssessmentId ? { assessment_id: resolvedAssessmentId } : {}),
    };

    try {
      const data = await request({
        url: API_ROUTES?.Chatbot?.sendChatMessage,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        data: payload,
      });

      if (data?.conversation_id) {
        setConversationId(data.conversation_id);
      }

      const assistantMessage = {
        sender: "assistant" as const,
        text: data?.response || "No response from bot.",
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      console.error("Error sending message:", err);
      setMessages((prev) => [
        ...prev,
        {
          sender: "assistant" as const,
          text: "Something went wrong. Please try again.",
        },
      ]);
    }
  };

  const handleHistory = async () => {
    setActiveTab("history");
    try {
      const response = await request({
        url: `${API_ROUTES?.Chatbot?.getChatHistory}?created_by=${userName}`,
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          //Authorization: `Bearer ${token}`, // token from hook
        },
      });

      setHistory(response?.data);
    } catch (err) {
      console.error("Error in Fetch History:", err);
    }
  };

  const searchHistoryData =
    getHistroy != null
      ? getHistroy?.filter((row: any) =>
        Object.values(row)
          .join(" ")
          .toLowerCase()
          .includes(searchTerm.toLowerCase()),
      )
      : [];

  const handleHistroyItemClick = async (historyMsg: any) => {
    setMessages([]);
    setConversationId(historyMsg?.conversation_id ?? null);
    const prompt = { sender: "user" as const, text: historyMsg?.prompt };
    setMessages((prev) => [...prev, prompt]);

    const userMessage = {
      sender: "assistant" as const,
      text: historyMsg?.response,
    };
    setMessages((prev) => [...prev, userMessage]);
    setActiveTab("chat");
  };

  const handleNewChat = async () => {
    setMessages([]);
    setConversationId(null);
    setActiveTab("chat");
  };

  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        transition: "all 0.3s ease",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <AppBar position="static" sx={{ backgroundColor: "#F4F7FE" }}>
        <Toolbar sx={{ justifyContent: "space-between", px: 1.5, minHeight: 64 }}>
          <Box>
            <Stack direction="row" spacing={2}>

              <Button
                onClick={() => setActiveTab("chat")}
                sx={{
                  color: activeTab === "chat" ? "#fff" : "#000",
                  backgroundColor: activeTab === "chat" ? "#4F81BD" : "transparent",
                  border: activeTab === "chat" ? "1px solid #3A6BA5" : "1px solid #ccc",
                  textTransform: "none",
                }}
              >
                AI Chat
              </Button>

              {/*<Button  variant={activeTab === "prompts" ? "contained" : "outlined"}
               onClick={() => setActiveTab("prompts")}>Auto Task</Button>*/}
            </Stack>
          </Box>

          {/* <Typography variant="h6">AI Chat</Typography> */}

          <Box sx={{ marginLeft: "auto", display: "flex", gap: 1 }}>

            <Tooltip title="New Chat">
              <IconButton
                onClick={handleNewChat}
                sx={{
                  color: "#4F81BD",     // icon color
                  "&:hover": {
                    backgroundColor: "transparent", // no hover background
                  },
                }}
              >
                <MapsUgcIcon />
              </IconButton>
            </Tooltip>


            <Tooltip title="History">
              <IconButton sx={{
                color: "#4F81BD",     // icon color
                "&:hover": {
                  backgroundColor: "transparent", // no hover background
                },
              }} color="primary" onClick={handleHistory}>
                <HistoryIcon />
              </IconButton>
            </Tooltip>
            {/* {!isMaximized ? (
              <Tooltip title={isMaximized ? "Minimize" : "Maximize"}>
                <IconButton
                  size="small"
                  color="secondary"
                  onClick={() => {
                    setIsMaximized(true);
                    onMaximize();
                  }}
                >
                  <FullscreenIcon />
                </IconButton>
              </Tooltip>
            ) : (
              <Tooltip title={isMaximized ? "Minimize" : "Maximize"}>
                <IconButton
                  size="small"
                  color="primary"
                  onClick={() => {
                    setIsMaximized(false);
                    onRestore();
                  }}
                >
                  <FullscreenExitIcon />
                </IconButton>
              </Tooltip>
            )} */}
            {!hideCloseButton && (
              <Tooltip title="Close">
                <IconButton size="small" color="secondary" onClick={onClose}>
                  <CloseIcon />
                </IconButton>
              </Tooltip>
            )}
          </Box>
        </Toolbar>
      </AppBar>
      {/* Chat Area */}
      {activeTab === "chat" ? (
        <Box sx={{ flex: 1, overflowY: "auto", px: 1.5, py: 1 }}>
      {messages.map((msg, index) => (
        <Box
          key={index}
          sx={{
            display: "flex",
            justifyContent:
              msg.sender === "user" ? "flex-end" : "flex-start",
            mb: 1.5,
          }}
        >
          <Stack
            direction="row"
            alignItems="flex-end"
            sx={{
              maxWidth: "86%",
              flexDirection: msg.sender === "user" ? "row-reverse" : "row",
            }}
          >
            <Box
              sx={{
                width: 34,
                height: 34,
                minWidth: 34,
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                mt: msg.sender === "user" ? 0 : 0.2,
                mb: msg.sender === "user" ? 0.15 : 0,
                bgcolor: msg.sender === "user" ? "#23406a" : "#eef5ff",
                color: msg.sender === "user" ? "#ffffff" : "#2c5f9e",
                border: msg.sender === "user" ? "1px solid #23406a" : "1px solid #cfe0f6",
                boxShadow: "0 4px 12px rgba(15, 23, 42, 0.08)",
              }}
            >
              {msg.sender === "user" ? (
                <PersonOutlineOutlinedIcon sx={{ fontSize: 18 }} />
              ) : (
                <SmartToyOutlinedIcon sx={{ fontSize: 18 }} />
              )}
            </Box>

            <Paper
              sx={{
                px: 1.4,
                py: 1.15,
                maxWidth: "100%",
                bgcolor: msg.sender === "user" ? "#5f5f60" : "#f7f9fc",
                color: msg.sender === "user" ? "#ffffff" : "#172554",
                border: msg.sender === "user" ? "none" : "1px solid #dbe6f2",
                borderRadius:
                  msg.sender === "user"
                    ? "18px 18px 6px 18px"
                    : "18px 18px 18px 6px",
                boxShadow: "0 10px 24px rgba(15, 23, 42, 0.06)",
                textAlign: "left",
                lineHeight: 1.55,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: "0.95rem",
                mr: msg.sender === "user" ? 0.15 : 0,
                ml: msg.sender === "user" ? 0 : 0.15,
              }}
            >
              {msg.text}
            </Paper>
          </Stack>
        </Box>
      ))}
      {loading && (
        <Typography variant="body2" sx={{ ml: 1 }}>
          Bot is typing...
        </Typography>
      )}
      {error && <Typography color="error">{error}</Typography>}
    </Box>
  ) : activeTab === "prompts" ? (
    <Box sx={{ flex: 1, overflowY: "auto", px: 1.5, py: 1 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1, // optional spacing between input and icon
        }}
      >
        <TextField
          placeholder="Search"
          variant="outlined"
          sx={{ flexGrow: 1 }} // Makes it fill available space
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
          }}
        />
        <IconButton color="primary">
          <MoreHorizIcon />
        </IconButton>
      </Box>
      {/* Prompts screen content here */}
      <Box
        sx={{
          mt: 1,
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        <Accordion
          title="Sample Accordion"
          expandIcon={<ExpandMoreIcon />}
          sx={{
            flexDirection: "row-reverse", // Places the arrow on the left
            "& .MuiAccordionSummary-expandIconWrapper": {
              mr: 2, // Adds margin to the right of the icon
            },
          }}
          extraContent={
            <Box sx={{ ml: "auto", mt: 0.5 }}>
              <Rating
                name="simple-controlled"
                value={ratingValue}
                max={1}
                onChange={(_event: any, newValue: any) => {
                  setRatingValue(newValue);
                }}
                size="small"
              />
            </Box>
          }
        >
          <Paper className="mx-2 p-4 mb-2">
            <Box sx={{ mb: 2, display: "flex", gap: 1 }}>
              {visibleChips.map((label) => (
                <Chip
                  label={label}
                  onClick={() => { }}
                  sx={{
                    backgroundColor: "#e9e9ed",
                    color: "#3b3b4f",
                    fontWeight: 300,
                    borderRadius: "10px",
                  }}
                />
              ))}
              {hiddenCount > 0 && (
                <Chip
                  label={`+${hiddenCount}`}
                  sx={{
                    backgroundColor: "#e9e9ed",
                    color: "#3b3b4f",
                    fontWeight: 300,
                    borderRadius: "13px",
                  }}
                />
              )}
            </Box>
          </Paper>
          <Typography sx={{ textAlign: "left" }}>Test Content</Typography>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              flexGrow: 1,
              mt: 3,
            }}
          >
            <Button
              variant="contained"
              color="primary"
              size="small"
              sx={{ alignSelf: "flex-end" }}
            >
              Try it
            </Button>
          </Box>
        </Accordion>
        <Accordion
          title="Sample Accordion"
          expandIcon={<ExpandMoreIcon />}
          sx={{
            flexDirection: "row-reverse", // Places the arrow on the left
            "& .MuiAccordionSummary-expandIconWrapper": {
              mr: 2, // Adds margin to the right of the icon
            },
          }}
        >
          <Typography>Accordion Text</Typography>
        </Accordion>
      </Box>
    </Box>
  ) : activeTab === "history" ? (
    <Box sx={{ flex: 1, overflowY: "auto", px: 1.5, py: 1 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1, // optional spacing between input and icon
        }}
      >
        <TextField
          placeholder="Search"
          variant="outlined"
          sx={{ flexGrow: 1 }} // Makes it fill available space
          onChange={(e: any) => setSearchTerm(e.target.value)}
          value={searchTerm}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon />
              </InputAdornment>
            ),
          }}
        />
        {/* <IconButton color="primary">
            <MoreHorizIcon />
          </IconButton> */}
      </Box>
      {/* History screen content here */}
      <Box sx={{ mt: 2 }}>
        <Typography variant="h6" textAlign={"left"}>
          History
        </Typography>
        {searchHistoryData?.map((history: any) => (
          <>
            <Stack
              key={history.id}
              mt={2}
              direction="row"
              alignItems="flex-start"
              spacing={1}
              sx={{
                cursor: "pointer",
                "&:hover": {
                  backgroundColor: "#f5f5f5",
                  borderRadius: "6px",
                },
                p: 1,
              }}
              onClick={() => handleHistroyItemClick(history)}
            >
              <ChatBubbleOutlineIcon
                fontSize="small"
                sx={{ mt: "3px" }}
                color="action"
              />
              <Typography
                sx={{
                  textAlign: "left",
                  lineHeight: 1.4,
                  wordBreak: "break-all",
                }}
              >
                {history?.prompt}
              </Typography>
            </Stack>
          </>
        ))}
      </Box>
    </Box>
  ) : (
    ""
  )
}
{/* Input Field */ }
{
  activeTab === "chat" && (
    <Box
      display="flex"
      borderTop="1px solid #ccc"
      sx={{ backgroundColor: "#F4F7FE", px: 1.5, py: 1 }}
    >
      <TextField
        fullWidth
        placeholder="Type your message..."
        value={input}
        onChange={(e: any) => setInput(e.target.value)}
        onKeyDown={(e: any) => e.key === "Enter" && handleSend()}
        sx={{ marginRight: "8px" }}
      />
      <IconButton sx={{ backgroundColor: "#4F81BD", color: "#fff" }} onClick={handleSend} disabled={loading}>
        <SendIcon />
      </IconButton>
    </Box>
  )
}
{/* Footer */ }
{/* {activeTab === "chat" && (
        <AppBar
          position="relative"
          sx={{
            top: "auto",
            bottom: 0,
            bgcolor: "primary",
            textAlign: "center",
            p: 1,
          }}
          elevation={0}
        >
          <Typography
            variant="caption"
            sx={{
              textAlign: "left",
              lineHeight: 1.4,
            }}
          >
            Exercise professional judgement while reviewing AI Responses as they
            may be inaccuratem.
          </Typography>
        </AppBar>
      )} */}
    </Box >
  );
};

export default Chatbot;
