import React, { useEffect, useRef, useState } from "react";
import {
  Toolbar,
  Typography,
  Box,
  Paper,
  Stack,
  InputAdornment,
  Menu,
  MenuItem,
  ListItemIcon,
  Snackbar,
  Chip,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import SmartToyOutlinedIcon from "@mui/icons-material/SmartToyOutlined";
import PersonOutlineOutlinedIcon from "@mui/icons-material/PersonOutlineOutlined";
import {
  Button,
  IconButton,
  TextField,
  AppBar,
  Tooltip,
} from "../remote-ui-stubs";
import MapsUgcIcon from "@mui/icons-material/MapsUgc";
import TimerOutlinedIcon from "@mui/icons-material/TimerOutlined";
import HistoryIcon from "@mui/icons-material/History";
import SearchIcon from "@mui/icons-material/Search";
import { useAxiosApi } from "../hooks/useAxiosApi";
import { useAuthToken } from "../hooks/useAuthToken";
import { streamChat, stageLabel, type ServerTimings } from "../hooks/useChatStream";
import { chatStrings } from "../utils/chatText";
import AuthGate from "./AuthGate";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import CloseIcon from "@mui/icons-material/Close";
import PictureAsPdfOutlinedIcon from "@mui/icons-material/PictureAsPdfOutlined";
import FileDownloadOutlinedIcon from "@mui/icons-material/FileDownloadOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import ThumbUpAltOutlinedIcon from "@mui/icons-material/ThumbUpAltOutlined";
import ThumbUpAltIcon from "@mui/icons-material/ThumbUpAlt";
import ThumbDownAltOutlinedIcon from "@mui/icons-material/ThumbDownAltOutlined";
import ThumbDownAltIcon from "@mui/icons-material/ThumbDownAlt";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import { downloadChatText, printChatAsPdf } from "../utils/exportChat";
import MarkdownMessage from "./MarkdownMessage";

// Number of prior messages sent back as conversation context. Bounded so the
// rephrase prompt cannot grow without limit on long conversations.
const MAX_HISTORY_MESSAGES = 10;

/** How long a turn took, measured in the browser from the moment Enter was pressed. */
interface TurnTiming {
  totalMs: number;
  /** Until the first word of the answer appeared. Absent if the answer was not streamed. */
  firstWordMs?: number;
  server?: ServerTimings;
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)}s`;

/** "Answered in 9.4s - first words 4.2s - understand 2.1s, search 0.4s, ..." */
function describeTiming({ totalMs, firstWordMs, server }: TurnTiming): string {
  const parts = [`Answered in ${seconds(totalMs)}`];
  if (firstWordMs !== undefined) parts.push(`first words after ${seconds(firstWordMs)}`);
  if (server) {
    // Stages that took a noticeable time, in the order they run.
    const stages: [string, number | undefined][] = [
      ["understanding the question", server.rephrase_ms],
      ["search", server.retrieve_ms],
      ["reranking", server.rerank_ms],
      ["writing the answer", server.generate_ms],
      ["translation", server.translate_ms],
      ["links", server.linkify_ms],
    ];
    const shown = stages
      .filter(([, ms]) => ms !== undefined && ms >= 50)
      .map(([label, ms]) => `${label} ${seconds(ms as number)}`);
    if (shown.length) parts.push(shown.join(", "));
    if (server.total_ms !== undefined) {
      // Whatever the server did not account for: the network, and rendering.
      parts.push(`network ${seconds(Math.max(0, totalMs - server.total_ms))}`);
    }
  }
  return parts.join(" · ");
}

const TIMINGS_KEY = "chatbot.showTimings";

interface ChatbotProps {
  messages: { sender: "user" | "assistant"; text: string; timing?: TurnTiming; historyId?: number }[];
  setMessages: React.Dispatch<React.SetStateAction<any[]>>;
  onClose?: () => void;
  hideCloseButton?: boolean;
  API_ROUTES?: any;
  /** A token from a host app that already authenticated the user. When given, this
   * component never shows its own sign-in form - a 401 on a bad/expired host token is
   * the host's problem to fix, not something to paper over with a second login flow. */
  authToken?: string;
}

const Chatbot: React.FC<ChatbotProps> = ({
  messages,
  setMessages,
  onClose,
  hideCloseButton,
  API_ROUTES,
  authToken,
}) => {
  // Only used when no host token was supplied - see the authToken prop above.
  const auth = useAuthToken();
  const effectiveToken = authToken ?? auth.token;
  const authHeaders: Record<string, string> = effectiveToken ? { Authorization: `Bearer ${effectiveToken}` } : {};
  const showAuthGate = !authToken && auth.authRequired;
  // Derived the same way streamUrl/sessionsUrl below are, for a host app that only knows
  // the older API_ROUTES shape.
  const authUrl =
    API_ROUTES?.Chatbot?.authenticate ??
    (API_ROUTES?.Chatbot?.sendChatMessage
      ? API_ROUTES.Chatbot.sendChatMessage.replace(/\/chat\/?$/, "/auth/authenticate")
      : undefined);
  const meUrl =
    API_ROUTES?.Chatbot?.me ??
    (API_ROUTES?.Chatbot?.sendChatMessage
      ? API_ROUTES.Chatbot.sendChatMessage.replace(/\/chat\/?$/, "/chat/me")
      : undefined);

  // Shows the sign-in form right away instead of waiting for the first send/history request to
  // 401 - GET /chat/me is cheap and answers exactly this: 401 means a token is needed (with
  // none stored, or an expired one), 503 means DISABLE_AUTH is on (no login needed, stay
  // quiet), 200 means the stored token still works. Skipped when a host already supplies a
  // token - a bad host token is the host's problem, not something to second-guess here.
  useEffect(() => {
    if (authToken || !meUrl) return;
    let cancelled = false;
    fetch(meUrl, { headers: authHeaders })
      .then((response) => {
        if (!cancelled && response.status === 401) auth.markAuthRequired();
      })
      .catch(() => {
        /* offline/unreachable: the first real request will surface it if it matters */
      });
    return () => {
      cancelled = true;
    };
    // Once, on mount - not on every authHeaders change, or a fresh login would immediately
    // re-trigger this same check against a token that is now valid.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [getHistroy, setHistory] = useState<any[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  // Streaming turn in flight. `loading` from useAxiosApi only covers the
  // blocking fallback, so the send button tracks both.
  const [isStreaming, setIsStreaming] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  // What the progress line is worded by: the language of the question being answered.
  const lastUserText = [...messages].reverse().find((m) => m.sender === "user")?.text ?? "";
  const [activeTab, setActiveTab] = useState<"chat" | "history">("chat");
  const [searchTerm, setSearchTerm] = useState("");
  // Show how long each answer took. Remembered across reloads.
  const [showTimings, setShowTimings] = useState(() => {
    try {
      return localStorage.getItem(TIMINGS_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggleTimings = () => {
    setShowTimings((prev) => {
      try {
        localStorage.setItem(TIMINGS_KEY, prev ? "0" : "1");
      } catch {
        /* storage unavailable: the choice just lasts for this session */
      }
      return !prev;
    });
  };
  // Export menu, and the message shown if an export fails.
  const [exportAnchor, setExportAnchor] = useState<HTMLElement | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const userName = localStorage.getItem("user"); //|| "emmanullah.i@in.ey.com";

  // Thumbs up/down per chat turn (msg.historyId -> what was submitted), and which turn's
  // "add a comment" box is currently open. Kept out of `messages` (a prop shared with the host
  // shell) since this is this component's own UI state, not part of the transcript.
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);

  const [feedbackByTurn, setFeedbackByTurn] = useState<Record<number, { rating: "positive" | "negative"; comment?: string }>>({});
  const [commentBoxFor, setCommentBoxFor] = useState<number | null>(null);
  const [commentDraft, setCommentDraft] = useState("");
  const feedbackApi = useAxiosApi();

  const submitFeedback = async (historyId: number, rating: "positive" | "negative", comment?: string) => {
    const feedbackUrl = API_ROUTES?.Chatbot?.sendChatMessage
      ? `${API_ROUTES.Chatbot.sendChatMessage}/history/${historyId}/feedback`
      : undefined;
    try {
      await feedbackApi.request({
        url: feedbackUrl,
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        data: comment ? { rating, comment } : { rating },
      });
      setFeedbackByTurn((prev) => ({ ...prev, [historyId]: { rating, comment } }));
    } catch (err: any) {
      if (err?.response?.status === 401 && !authToken) auth.markAuthRequired();
      else setNotice("Could not record feedback.");
    }
  };

  // Whether a turn's citations actually held up, once the after-the-fact grounding check has
  // run (Settings.GROUNDING_CHECK_ENABLED - off by default, so this is usually empty). Only
  // "partial"/"unsupported" turns are ever stored here; a "grounded" one has nothing to show.
  const [groundingByTurn, setGroundingByTurn] = useState<Record<number, { verdict: string; detail?: string }>>({});

  // The check runs in the background after the answer is already on screen, so there is nothing
  // to show yet when the turn completes. One delayed re-check of that one row is enough to pick
  // up a verdict that lands soon after - GET /chat/history?id=... already returns it.
  const GROUNDING_POLL_DELAY_MS = 6000;
  const pollGrounding = (historyId: number) => {
    const historyUrl = API_ROUTES?.Chatbot?.getChatHistory;
    if (!historyUrl) return;
    setTimeout(async () => {
      try {
        const response = await feedbackApi.request({
          url: historyUrl,
          method: "GET",
          params: { id: historyId, page_size: 1 },
          headers: { "Content-Type": "application/json", ...authHeaders },
        });
        const row = response?.data?.[0];
        if (row?.grounding_verdict && row.grounding_verdict !== "grounded") {
          setGroundingByTurn((prev) => ({
            ...prev, [historyId]: { verdict: row.grounding_verdict, detail: row.grounding_detail },
          }));
        }
      } catch {
        /* best-effort: a missed grounding badge is not worth surfacing an error for */
      }
    }, GROUNDING_POLL_DELAY_MS);
  };

  const rateGood = (historyId?: number) => {
    if (!historyId) return;
    setCommentBoxFor(null);
    void submitFeedback(historyId, "positive");
  };

  // Thumbs-down is recorded right away, same as thumbs-up; the comment box that opens next is
  // for an optional detail, not a gate on the rating itself.
  const rateBad = (historyId?: number) => {
    if (!historyId) return;
    void submitFeedback(historyId, "negative");
    setCommentDraft("");
    setCommentBoxFor(historyId);
  };

  const submitComment = (historyId: number) => {
    const comment = commentDraft.trim();
    setCommentBoxFor(null);
    if (comment) void submitFeedback(historyId, "negative", comment);
  };

  // Follow the answer as it is written, unless the reader has scrolled up.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);
  const handleScroll = () => {
    const el = scrollRef.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, stage, isStreaming, activeTab]);

  const { request, loading, error } = useAxiosApi();
  const handleSend = async (overrideText?: string) => {
    const textToSend = (typeof overrideText === "string" ? overrideText : input).trim();
    if (!textToSend || isStreaming || loading) return;
    stickToBottom.current = true;
    // The stopwatch starts here, at Enter, and stops once the whole answer is in.
    const startedAt = performance.now();
    let firstWordAt: number | undefined;

    const userMessage = { sender: "user" as const, text: textToSend };
    // `messages` holds the transcript as it stood before this turn, so the new
    // question is appended explicitly rather than being read back from state.
    const history = messages.slice(-MAX_HISTORY_MESSAGES).map((message) => ({
      role: message.sender === "user" ? "user" : "assistant",
      content: message.text,
    }));
    setMessages((prev) => [...prev, userMessage]);
    setInput("");

    const payload = {
      conversation_id: conversationId,
      user_id: userName,
      // The greeting is written by the bot, which has no clock of the user's own.
      local_hour: new Date().getHours(),
      chat: [...history, { role: "user", content: textToSend }],
    };

    // The assistant bubble is created by the first write rather than up front,
    // so nothing empty sits on screen during the retrieval stage. After that it
    // is always the last message, since sending is blocked while a turn runs.
    let hasBubble = false;
    const writeAssistant = (text: string, timing?: TurnTiming, historyId?: number) => {
      setMessages((prev) => {
        if (!hasBubble) {
          return [...prev, { sender: "assistant" as const, text, timing, historyId }];
        }
        const next = [...prev];
        next[next.length - 1] = {
          ...next[next.length - 1],
          text,
          ...(timing ? { timing } : {}),
          ...(historyId ? { historyId } : {}),
        };
        return next;
      });
      hasBubble = true;
    };

    // Called once the full answer is in hand.
    const finishTiming = (server?: ServerTimings): TurnTiming => {
      const timing: TurnTiming = {
        totalMs: Math.round(performance.now() - startedAt),
        firstWordMs:
          firstWordAt !== undefined ? Math.round(firstWordAt - startedAt) : undefined,
        server,
      };
      console.info(`[chat timing] ${describeTiming(timing)}`, timing);
      return timing;
    };

    // The host shell supplies API_ROUTES in production and may not know about
    // the streaming route yet, so derive it from the chat URL when absent.
    const streamUrl =
      API_ROUTES?.Chatbot?.streamChatMessage ??
      (API_ROUTES?.Chatbot?.sendChatMessage
        ? `${API_ROUTES.Chatbot.sendChatMessage}/stream`
        : undefined);

    setSuggestedQuestions([]);
    setIsStreaming(true);
    setStage(null);
    let accumulated = "";
    // The window's own notices are worded in the language the question was written in.
    const words = chatStrings(textToSend);

    let unauthorized = false;
    try {
      const streamed = streamUrl
        ? await streamChat(
            streamUrl,
            payload,
            {
              onMeta: (meta) => {
                if (meta?.conversation_id) setConversationId(meta.conversation_id);
              },
              onStage: setStage,
              onDelta: (text) => {
                if (firstWordAt === undefined) firstWordAt = performance.now();
                accumulated += text;
                writeAssistant(accumulated);
              },
              onDone: (data) => {
                if (data?.conversation_id) setConversationId(data.conversation_id);
                // Authoritative text: translated if the question was not in
                // English, and with citations linkified.
                writeAssistant(
                  data?.response || accumulated || words.noResponse,
                  finishTiming(data?.timings),
                  data?.history_id,
                );
                if (data?.history_id) pollGrounding(data.history_id);
                if (data?.suggested_questions?.length) setSuggestedQuestions(data.suggested_questions);
              },
              onError: (detail, reason) => {
                // The server's own message is English and technical: for the console only.
                if (detail) console.error("Chat stream error:", detail);
                const notice = reason === "interrupted" ? words.interrupted : words.couldNotAnswer;
                writeAssistant(accumulated ? `${accumulated}\n\n${notice}` : notice);
              },
              onUnauthorized: () => {
                unauthorized = true;
                if (!authToken) auth.markAuthRequired();
              },
            },
            undefined,
            authHeaders,
          )
        : false;

      if (!streamed && !unauthorized) {
        // Streaming endpoint unreachable (older backend, proxy in the way).
        // Fall back to the blocking one so the chat still works.
        const data = await request({
          url: API_ROUTES?.Chatbot?.sendChatMessage,
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders },
          data: payload,
        });

        if (data?.conversation_id) {
          setConversationId(data.conversation_id);
        }
        writeAssistant(data?.response || words.noResponse, finishTiming(data?.timings), data?.history_id);
        if (data?.history_id) pollGrounding(data.history_id);
        if (data?.suggested_questions?.length) setSuggestedQuestions(data.suggested_questions);
      }
    } catch (err: any) {
      if (err?.response?.status === 401) {
        if (!authToken) auth.markAuthRequired();
      } else {
        console.error("Error sending message:", err);
        writeAssistant(words.wentWrong);
      }
    } finally {
      setIsStreaming(false);
      setStage(null);
    }
  };

  // The history list shows conversations (chat_session, one row per chat opened), never
  // individual turns - clicking one loads every prompt/response in it, oldest first, so a
  // five-message conversation is one entry in the list, not five.
  const HISTORY_LIST_PAGE_SIZE = 100;
  // GET /chat/session; derived from getChatHistory (like streamUrl above) so a host app
  // that only knows the older endpoint still gets the grouped list.
  const sessionsUrl =
    API_ROUTES?.Chatbot?.getChatSession ??
    (API_ROUTES?.Chatbot?.getChatHistory
      ? API_ROUTES.Chatbot.getChatHistory.replace(/\/history\/?$/, "/session")
      : undefined);

  const handleHistory = async () => {
    setActiveTab("history");
    try {
      const response = await request({
        url: sessionsUrl,
        method: "GET",
        params: { page_size: HISTORY_LIST_PAGE_SIZE, is_active: true },
        headers: { "Content-Type": "application/json", ...authHeaders },
      });
      setHistory(response?.data ?? []);
    } catch (err: any) {
      if (err?.response?.status === 401 && !authToken) auth.markAuthRequired();
      else console.error("Error in Fetch History:", err);
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

  /** Every turn of `conversationId`, oldest first. Loops pages - a conversation can hold more than one. */
  const fetchConversationTurns = async (conversationId: number): Promise<any[]> => {
    const turns: any[] = [];
    let pageNumber = 1;
    for (;;) {
      const response = await request({
        url: API_ROUTES?.Chatbot?.getChatHistory,
        method: "GET",
        params: { conversation_id: conversationId, page_number: pageNumber, page_size: HISTORY_LIST_PAGE_SIZE },
        headers: { "Content-Type": "application/json", ...authHeaders },
      });
      turns.push(...(response?.data ?? []));
      const total = response?.total_records ?? turns.length;
      // filter_chatbot_history returns newest first; fetched in that order, page after page,
      // the whole list is still newest-first, so it is reversed once, after the loop.
      if (turns.length >= total || !response?.data?.length) break;
      pageNumber += 1;
    }
    return turns.reverse();
  };

  const handleHistroyItemClick = async (session: any) => {
    const id = session?.id;
    if (!id) return;
    setActiveTab("chat");
    setConversationId(id);
    setMessages([]);
    try {
      const turns = await fetchConversationTurns(id);
      const rebuilt = turns.flatMap((turn) => {
        const words = chatStrings(turn?.prompt ?? "");
        const user = { sender: "user" as const, text: turn?.prompt ?? "" };
        // A turn that failed has no response - say so, rather than a user message with no reply.
        const text = turn?.response || (turn?.error ? words.couldNotAnswer : null);
        return text ? [user, { sender: "assistant" as const, text, historyId: turn?.id }] : [user];
      });
      setMessages(rebuilt);
      // Replaced wholesale, not merged: these are a different conversation's turns, and any
      // pending poll from the one just left behind should not paint a badge onto this one.
      const grounding: Record<number, { verdict: string; detail?: string }> = {};
      for (const turn of turns) {
        if (turn?.id && turn?.grounding_verdict && turn.grounding_verdict !== "grounded") {
          grounding[turn.id] = { verdict: turn.grounding_verdict, detail: turn.grounding_detail };
        }
      }
      setGroundingByTurn(grounding);
    } catch (err: any) {
      if (err?.response?.status === 401 && !authToken) {
        auth.markAuthRequired();
      } else {
        console.error("Error loading conversation:", err);
        setNotice("Could not load this chat.");
      }
    }
  };

  const canExport = messages.length > 0 && !isStreaming;
  const runExport = async (action: () => void | Promise<unknown>) => {
    setExportAnchor(null);
    try {
      await action();
    } catch (err) {
      console.error("Export failed:", err);
      setNotice("Could not export the chat.");
    }
  };

  const handleNewChat = async () => {
    setMessages([]);
    setConversationId(null);
    setSuggestedQuestions([]);
    setActiveTab("chat");
  };

  /** Thumbs up/down for one answer, plus the "what went wrong" box a thumbs-down opens. */
  const renderFeedback = (historyId?: number): React.ReactNode => {
    if (!historyId) return null;
    const state = feedbackByTurn[historyId];
    return (
      <>
        <Box dir="ltr" sx={{ mt: 0.6, display: "flex", alignItems: "center", gap: 0.2 }}>
          <Tooltip title="Good response">
            <IconButton size="small" onClick={() => rateGood(historyId)} sx={{ p: 0.3 }}>
              {state?.rating === "positive" ? (
                <ThumbUpAltIcon sx={{ fontSize: 15, color: "#2c5f9e" }} />
              ) : (
                <ThumbUpAltOutlinedIcon sx={{ fontSize: 15, color: "#9aa8bd" }} />
              )}
            </IconButton>
          </Tooltip>
          <Tooltip title="Bad response">
            <IconButton size="small" onClick={() => rateBad(historyId)} sx={{ p: 0.3 }}>
              {state?.rating === "negative" ? (
                <ThumbDownAltIcon sx={{ fontSize: 15, color: "#b3441f" }} />
              ) : (
                <ThumbDownAltOutlinedIcon sx={{ fontSize: 15, color: "#9aa8bd" }} />
              )}
            </IconButton>
          </Tooltip>
          {groundingByTurn[historyId] && (
            <Tooltip
              title={
                groundingByTurn[historyId].detail
                  || "AI can make mistakes"
              }
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.3, ml: 0.3, color: "#a15c00" }}>
                <WarningAmberOutlinedIcon sx={{ fontSize: 15 }} />
                <Typography component="span" sx={{ fontSize: "0.7rem" }}>
                   
                </Typography>
              </Box>
            </Tooltip>
          )}
        </Box>
        {commentBoxFor === historyId && (
          <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 0.5 }} dir="ltr">
            <TextField
              size="small"
              autoFocus
              fullWidth
              placeholder="What went wrong? (optional)"
              value={commentDraft}
              onChange={(e: any) => setCommentDraft(e.target.value)}
              onKeyDown={(e: any) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  submitComment(historyId);
                }
              }}
              sx={{ "& .MuiInputBase-input": { fontSize: "0.8rem", py: 0.5 } }}
            />
            <IconButton size="small" onClick={() => submitComment(historyId)} sx={{ p: 0.3 }}>
              <SendIcon sx={{ fontSize: 15 }} />
            </IconButton>
            <IconButton size="small" onClick={() => setCommentBoxFor(null)} sx={{ p: 0.3 }}>
              <CloseIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </Stack>
        )}
      </>
    );
  };

  return (
    <Box
      sx={{
        position: "relative",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        transition: "all 0.3s ease",
        overflow: "hidden",
      }}
    >
      {showAuthGate && (
        <AuthGate
          error={auth.loginError}
          loading={auth.loggingIn}
          onSubmit={(email, password) => authUrl && auth.login(authUrl, email, password)}
        />
      )}
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
            </Stack>
          </Box>

          {/* <Typography variant="h6">AI Chat</Typography> */}

          <Box sx={{ marginLeft: "auto", display: "flex", gap: 1 }}>

            <Tooltip title={showTimings ? "Hide answer times" : "Show answer times"}>
              <IconButton
                onClick={toggleTimings}
                sx={{
                  color: showTimings ? "#4F81BD" : "#9aa7b8",
                  "&:hover": { backgroundColor: "transparent" },
                }}
              >
                <TimerOutlinedIcon />
              </IconButton>
            </Tooltip>

            <Tooltip title="Export chat">
              <span>
                <IconButton
                  onClick={(e: React.MouseEvent<HTMLElement>) => setExportAnchor(e.currentTarget)}
                  disabled={!canExport}
                  sx={{ color: "#4F81BD", "&:hover": { backgroundColor: "transparent" } }}
                >
                  <FileDownloadOutlinedIcon />
                </IconButton>
              </span>
            </Tooltip>

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
        <Box
          ref={scrollRef}
          onScroll={handleScroll}
          sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: 1.5, py: 1 }}
        >
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
              dir="auto"
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
                textAlign: "start", // follows dir="auto": right-aligned for Arabic, left for English
                lineHeight: 1.55,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: "0.95rem",
                mr: msg.sender === "user" ? 0.15 : 0,
                ml: msg.sender === "user" ? 0 : 0.15,
              }}
            >
              {msg.sender === "assistant" ? <MarkdownMessage text={msg.text} /> : msg.text}
              {msg.sender === "assistant" && renderFeedback(msg.historyId)}
              {showTimings && msg.timing && (
                <Typography
                  component="div"
                  dir="ltr"
                  sx={{ mt: 0.8, fontSize: "0.72rem", color: "#5b6b82", lineHeight: 1.35, textAlign: "left" }}
                >
                  {describeTiming(msg.timing)}
                </Typography>
              )}
            </Paper>
          </Stack>
        </Box>
      ))}
      {(loading || isStreaming) && (
        <Typography variant="body2" sx={{ ml: 1, color: "#5b6b82", fontStyle: "italic" }}>
          {stageLabel(stage, chatStrings(lastUserText))}
        </Typography>
      )}
      {error && <Typography color="error">{error}</Typography>}
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
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography
                  dir="auto"
                  sx={{
                    textAlign: "start",
                    lineHeight: 1.4,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {history?.name}
                </Typography>
                {history?.created_on && (
                  <Typography variant="caption" sx={{ color: "#5b6b82" }}>
                    {new Date(history.created_on).toLocaleString()}
                  </Typography>
                )}
              </Box>
            </Stack>
          </>
        ))}
      </Box>
    </Box>
  ) : (
    ""
  )
}
{/* Suggested questions */ }
{activeTab === "chat" && suggestedQuestions.length > 0 && !isStreaming && (
  <Box sx={{ px: 1.5, pt: 0.75, pb: 0.25, display: "flex", flexWrap: "wrap", gap: 0.75, flexShrink: 0 }}>
    {suggestedQuestions.map((q, i) => (
      <Chip
        key={i}
        label={q}
        size="small"
        onClick={() => handleSend(q)}
        sx={{
          fontSize: "0.75rem",
          cursor: "pointer",
          backgroundColor: "#eef5ff",
          color: "#2c5f9e",
          border: "1px solid #cfe0f6",
          "&:hover": { backgroundColor: "#dceeff" },
          maxWidth: "100%",
          height: "auto",
          "& .MuiChip-label": { whiteSpace: "normal", py: 0.5 },
        }}
      />
    ))}
  </Box>
)}
{/* Input Field */ }
{
  activeTab === "chat" && (
    <Box
      display="flex"
      borderTop="1px solid #ccc"
      sx={{ backgroundColor: "#F4F7FE", px: 1.5, py: 1, flexShrink: 0 }}
    >
      <TextField
        fullWidth
        placeholder="Type your message..."
        value={input}
        onChange={(e: any) => setInput(e.target.value)}
        onKeyDown={(e: any) => e.key === "Enter" && handleSend()}
        slotProps={{ htmlInput: { dir: "auto" } }}
        sx={{ marginRight: "8px" }}
      />
      <IconButton
        sx={{ backgroundColor: "#4F81BD", color: "#fff" }}
        onClick={handleSend}
        disabled={loading || isStreaming}
      >
        <SendIcon />
      </IconButton>
    </Box>
  )
}
      <Menu
        anchorEl={exportAnchor}
        open={Boolean(exportAnchor)}
        onClose={() => setExportAnchor(null)}
      >
        <MenuItem onClick={() => runExport(() => printChatAsPdf(messages, userName))}>
          <ListItemIcon><PictureAsPdfOutlinedIcon fontSize="small" /></ListItemIcon>
          Export as PDF
        </MenuItem>
        <MenuItem onClick={() => runExport(() => downloadChatText(messages, userName))}>
          <ListItemIcon><DescriptionOutlinedIcon fontSize="small" /></ListItemIcon>
          Download as text
        </MenuItem>
      </Menu>
      <Snackbar
        open={notice !== null}
        autoHideDuration={3000}
        onClose={() => setNotice(null)}
        message={notice ?? ""}
      />
    </Box >
  );
};

export default Chatbot;
