export const LANGUAGE_AUTO = "auto";

// The languages people here speak to Nudge, besides the one of their interface
const SPOKEN = ["ar", "en"];
const DEFAULT_TAGS = { ar: "ar-SA", en: "en-US" };
const SHORT_NAMES = { ar: "ع", en: "EN" };

const ARABIC_LETTER = /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/g;
const LETTER = /\p{L}/gu;

/**
 * @param {string} locale "ar-001", "en_US", "fr"
 * @returns {string} "ar", "en", "fr"; empty when there is none to read
 */
export function languageOf(locale) {
    const code = String(locale || "").trim().slice(0, 2).toLowerCase();
    return /^[a-z]{2}$/.test(code) ? code : "";
}

/**
 * @param {string} setting what the person chose: a language, or "auto"
 * @param {string} interfaceLocale language of their interface
 * @returns {string} the language they are taken to speak
 */
export function resolveLanguage(setting, interfaceLocale) {
    if (setting && setting !== LANGUAGE_AUTO) {
        return languageOf(setting) || "en";
    }
    return languageOf(interfaceLocale) || "en";
}

/**
 * @param {string} interfaceLocale
 * @returns {string[]} the languages offered, the one of the interface first
 */
export function languagesFor(interfaceLocale) {
    return [...new Set([languageOf(interfaceLocale), ...SPOKEN].filter(Boolean))];
}

export function nextLanguage(current, interfaceLocale) {
    const languages = languagesFor(interfaceLocale);
    return languages[(languages.indexOf(current) + 1) % languages.length];
}

// The dialects the person may choose to be listened to in: a tag the browser's recognition reads
export const ARABIC_VARIANTS = [
    "ar-SA", "ar-EG", "ar-AE", "ar-SY", "ar-IQ", "ar-JO", "ar-LB", "ar-PS", "ar-KW", "ar-QA",
    "ar-BH", "ar-OM", "ar-YE", "ar-SD", "ar-MA", "ar-DZ", "ar-TN",
];
export const ENGLISH_VARIANTS = ["en-US", "en-GB"];
const VARIANT = /^[a-z]{2}-[A-Z]{2}$/;

/**
 * @param {string} code
 * @param {string[]} [preferred] languages the browser asks websites for
 * @param {string} [chosen] what the person chose in Nudge: "auto", a language, or a dialect such as "ar-EG"
 * @returns {string} what the recognition of the browser is told to listen to.
 *   The dialect is the one the person chose in Nudge, else the one they chose for their browser.
 */
export function speechTag(code, preferred = [], chosen = "") {
    if (VARIANT.test(chosen || "") && languageOf(chosen) === code) {
        return chosen;
    }
    const own = preferred.find((tag) => String(tag).toLowerCase().startsWith(`${code}-`));
    return own || DEFAULT_TAGS[code] || code;
}

export function shortName(code) {
    return SHORT_NAMES[code] || String(code || "").toUpperCase();
}

/**
 * @param {string} text
 * @returns {string} "ar" when the text is mostly written in Arabic letters, "en" otherwise
 */
export function detectLanguage(text) {
    const letters = (String(text).match(LETTER) || []).length;
    const arabic = (String(text).match(ARABIC_LETTER) || []).length;
    return letters && arabic * 2 >= letters ? "ar" : "en";
}
