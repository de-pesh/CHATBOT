/**
 * The words the chat window itself says while an answer is on its way - progress
 * labels and failure notices - in the language the person is writing in, so an
 * Arabic conversation does not switch to English whenever something is loading
 * or goes wrong. The answers themselves come from the server already translated.
 */
export type ChatLang = "en" | "ar" | "hi";

export interface ChatStrings {
  stages: Record<string, string>;
  typing: string;
  noResponse: string;
  wentWrong: string;
  couldNotAnswer: string;
  interrupted: string;
}

const STRINGS: Record<ChatLang, ChatStrings> = {
  en: {
    stages: {
      rephrasing: "Understanding your question...",
      retrieving: "Searching the knowledge base...",
      reranking: "Refining the results...",
      generating: "Writing the answer...",
      finalising: "Finishing up...",
    },
    typing: "Bot is typing...",
    noResponse: "No response from bot.",
    wentWrong: "Something went wrong. Please try again.",
    couldNotAnswer: "The assistant could not answer. Please try again.",
    interrupted: "The connection to the assistant was interrupted.",
  },
  ar: {
    stages: {
      rephrasing: "جارٍ فهم سؤالك...",
      retrieving: "جارٍ البحث في قاعدة المعرفة...",
      reranking: "جارٍ تحسين النتائج...",
      generating: "جارٍ كتابة الإجابة...",
      finalising: "لمسات أخيرة...",
    },
    typing: "المساعد يكتب...",
    noResponse: "لا توجد استجابة من المساعد.",
    wentWrong: "حدث خطأ ما. يرجى المحاولة مرة أخرى.",
    couldNotAnswer: "تعذّر على المساعد الإجابة. يرجى المحاولة مرة أخرى.",
    interrupted: "انقطع الاتصال بالمساعد.",
  },
  hi: {
    stages: {
      rephrasing: "आपका प्रश्न समझा जा रहा है...",
      retrieving: "नॉलेज बेस में खोजा जा रहा है...",
      reranking: "परिणामों को बेहतर किया जा रहा है...",
      generating: "उत्तर लिखा जा रहा है...",
      finalising: "अंतिम रूप दिया जा रहा है...",
    },
    typing: "बॉट लिख रहा है...",
    noResponse: "बॉट से कोई प्रतिक्रिया नहीं मिली।",
    wentWrong: "कुछ गलत हो गया। कृपया पुनः प्रयास करें।",
    couldNotAnswer: "सहायक उत्तर नहीं दे सका। कृपया पुनः प्रयास करें।",
    interrupted: "सहायक से कनेक्शन टूट गया।",
  },
};

const ARABIC_LETTER = /[؀-ۿݐ-ݿ]/g;
const DEVANAGARI_LETTER = /[ऀ-ॿ]/g;
const LATIN_LETTER = /[A-Za-zÀ-ɏ]/g;
// Letters Persian and Urdu add to the Arabic alphabet (pe, che, zhe, gaf, keheh,
// Farsi yeh, tteh, ddal, rreh, noon ghunna, yeh barree, heh goal, do-chashmee heh).
// Text with them is written in Arabic letters but is not Arabic, and Arabic
// wording would be wrong for it, so it gets the default.
const NOT_ARABIC = /[پچژگکیٹڈڑںےہھ]/;

const count = (text: string, letters: RegExp) => (text.match(letters) ?? []).length;

/** Which of the languages above `text` is written in: by its script, English when unsure. */
export function chatLanguageOf(text: string): ChatLang {
  const arabic = NOT_ARABIC.test(text) ? 0 : count(text, ARABIC_LETTER);
  const hindi = count(text, DEVANAGARI_LETTER);
  const latin = count(text, LATIN_LETTER);
  if (arabic > latin && arabic >= hindi) return "ar";
  if (hindi > latin && hindi > arabic) return "hi";
  return "en";
}

/** The chat window's own wording for a person who last wrote `text`. */
export const chatStrings = (text: string): ChatStrings => STRINGS[chatLanguageOf(text)];
