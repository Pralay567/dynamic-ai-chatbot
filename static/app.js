
'use strict';

// ============================================================
// CONFIGURATION — NEVER PUT API KEYS IN FRONTEND CODE
// ============================================================

const GEMINI_CONFIG = {
    apiEndpoint: '/api/v1/chat',
    healthEndpoint: '/api/v1/health',
    conversationsEndpoint: '/api/v1/conversations',
    temperature: 0.7
};

const CONVERSATION_STORAGE_KEY = 'chatbot.conversation_id';
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

let conversationId = null;

function generateUuidV4() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }

    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(bytes, b =>
        b.toString(16).padStart(2, '0')
    ).join('');

    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function saveConversationId(id) {
    try {
        window.localStorage.setItem(CONVERSATION_STORAGE_KEY, id);
    } catch (error) {
        // Storage unavailable; use the in-memory ID.
    }
}

function loadOrCreateConversationId() {
    try {
        const stored = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);

        if (stored && UUID_V4_PATTERN.test(stored)) {
            return stored;
        }
    } catch (error) {
        // Create a new in-memory ID.
    }

    const created = generateUuidV4();
    saveConversationId(created);
    return created;
}

// ============================================================
// APPLICATION STATE
// ============================================================

const appState = {
    isBusy: false,
    currentSection: 'chat',
    conversationHistory: [],

    currentConversation: {
        messages: [],
        sentiment: 'positive',
        topics: [],
        startTime: Date.now()
    },

    analytics: {
        totalConversations: 1,
        totalMessages: 1,
        responseTimes: [],

        apiCalls: {
            successful: 0,
            failed: 0
        },

        sentimentCounts: {
            positive: 1,
            negative: 0,
            neutral: 0
        }
    },

    settings: {
        theme: 'auto',
        language: 'en',
        temperature: 0.7,
        showTimestamps: true,
        enableSentiment: true,
        contextMemory: true
    }
};

const fallbackResponses = {
    apiError: [
        "I'm having trouble connecting to my AI brain right now. Please try again in a moment!",
        "Oops! I'm experiencing some technical difficulties. Please try again.",
        "I'm temporarily offline. Please retry your question shortly."
    ],

    networkError: [
        "It seems there's a network issue. Please check your connection and try again.",
        "I can't reach my AI servers at the moment. Please try again when your connection is stable."
    ],

    genericError: [
        "Something unexpected happened. Please try again.",
        "I encountered an error while thinking. Could you please rephrase your question?"
    ]
};

const elements = {
    messageInput: null,
    sendBtn: null,
    chatMessages: null,
    typingIndicator: null,
    charCount: null,
    quickReplies: null,
    navItems: null,
    mobileNavItems: null,
    contentSections: null,
    themeToggle: null,
    themeSelect: null,
    apiStatus: null,
    sessionMessages: null,
    sessionSentiment: null,
    sessionDuration: null,
    apiCalls: null,
    recentTopics: null
};

// ============================================================
// INITIALIZATION
// ============================================================

document.addEventListener('DOMContentLoaded', function () {
    console.log('DOM loaded, initializing app...');

    initializeElements();
    initializeApp();

    conversationId = loadOrCreateConversationId();

    restoreConversation();
    setupEventListeners();
    updateAnalytics();
    startSessionTimer();
    testApiConnection();
});

function initializeElements() {
    elements.messageInput = document.getElementById('messageInput');
    elements.sendBtn = document.getElementById('sendBtn');
    elements.chatMessages = document.getElementById('chatMessages');
    elements.typingIndicator = document.getElementById('typingIndicator');
    elements.charCount = document.getElementById('charCount');

    elements.quickReplies = document.querySelectorAll('.quick-reply');
    elements.navItems = document.querySelectorAll('.nav-item');
    elements.mobileNavItems = document.querySelectorAll('.mobile-nav-item');
    elements.contentSections = document.querySelectorAll('.content-section');

    elements.themeToggle = document.getElementById('themeToggle');
    elements.themeSelect = document.getElementById('themeSelect');
    elements.apiStatus = document.getElementById('apiStatus');

    elements.sessionMessages = document.getElementById('sessionMessages');
    elements.sessionSentiment = document.getElementById('sessionSentiment');
    elements.sessionDuration = document.getElementById('sessionDuration');
    elements.apiCalls = document.getElementById('apiCalls');
    elements.recentTopics = document.getElementById('recentTopics');
}

function initializeApp() {
    const welcomeMessage = {
        id: Date.now(),
        text: "Hello! I'm your AI Assistant. I can help you with any questions, provide detailed explanations, assist with problems, and engage in meaningful conversations. How can I help you today?",
        sender: 'bot',
        timestamp: new Date(),
        sentiment: 'positive',
        isWelcome: true
    };

    appState.currentConversation.messages.push(welcomeMessage);
    renderMessage(welcomeMessage);
    updateAnalytics();
}

// ============================================================
// EVENT LISTENERS
// ============================================================

function setupEventListeners() {
    if (elements.messageInput) {
        elements.messageInput.addEventListener('input', handleInputChange);
        elements.messageInput.addEventListener('keypress', handleKeyPress);
    }

    if (elements.sendBtn) {
        elements.sendBtn.addEventListener('click', sendMessage);
    }

    elements.quickReplies.forEach(btn => {
        btn.addEventListener('click', e => {
            const message = e.currentTarget.getAttribute('data-message');

            if (elements.messageInput && message) {
                elements.messageInput.value = message;
                sendMessage();
            }
        });
    });

    elements.navItems.forEach(btn => {
        btn.addEventListener('click', e => {
            e.preventDefault();
            switchSection(e.currentTarget.getAttribute('data-section'));
        });
    });

    elements.mobileNavItems.forEach(btn => {
        btn.addEventListener('click', e => {
            e.preventDefault();
            switchSection(e.currentTarget.getAttribute('data-section'));
        });
    });

    if (elements.themeToggle) {
        elements.themeToggle.addEventListener('click', toggleTheme);
    }

    if (elements.themeSelect) {
        elements.themeSelect.addEventListener('change', e => {
            appState.settings.theme = e.target.value;
            applyTheme();
        });
    }

    setupSettingsListeners();
}

function setupSettingsListeners() {
    const languageSelect = document.getElementById('languageSelect');
    const temperatureSlider = document.getElementById('temperatureSlider');
    const temperatureValue = document.getElementById('temperatureValue');
    const showTimestamps = document.getElementById('showTimestamps');
    const enableSentiment = document.getElementById('enableSentiment');
    const contextMemory = document.getElementById('contextMemory');
    const exportChat = document.getElementById('exportChat');
    const clearChat = document.getElementById('clearChat');

    if (languageSelect) {
        languageSelect.addEventListener('change', e => {
            appState.settings.language = e.target.value;
        });
    }

    if (temperatureSlider && temperatureValue) {
        temperatureSlider.addEventListener('input', e => {
            const value = parseFloat(e.target.value);

            appState.settings.temperature = value;
            temperatureValue.textContent = value;
            GEMINI_CONFIG.temperature = value;
        });
    }

    if (showTimestamps) {
        showTimestamps.addEventListener('change', e => {
            appState.settings.showTimestamps = e.target.checked;
            rerenderMessages();
        });
    }

    if (enableSentiment) {
        enableSentiment.addEventListener('change', e => {
            appState.settings.enableSentiment = e.target.checked;
            rerenderMessages();
        });
    }

    if (contextMemory) {
        contextMemory.addEventListener('change', e => {
            appState.settings.contextMemory = e.target.checked;
        });
    }

    if (exportChat) {
        exportChat.addEventListener('click', exportChatHistory);
    }

    if (clearChat) {
        clearChat.addEventListener('click', clearChatHistory);
    }
}

function handleInputChange(e) {
    const length = e.target.value.length;

    if (!elements.charCount) return;

    elements.charCount.textContent = `${length}/1000`;

    if (length >= 950) {
        elements.charCount.style.color = 'var(--color-error)';
    } else if (length >= 900) {
        elements.charCount.style.color = 'var(--color-warning)';
    } else {
        elements.charCount.style.color = 'var(--color-text-secondary)';
    }
}

function handleKeyPress(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// ============================================================
// SEND MESSAGE
// ============================================================

async function sendMessage() {
    if (!elements.messageInput) return;

    const messageText = elements.messageInput.value.trim();

    if (!messageText || appState.isBusy) return;

    appState.isBusy = true;

    if (elements.sendBtn) {
        elements.sendBtn.disabled = true;
    }

    const userMessage = {
        id: Date.now(),
        text: messageText,
        sender: 'user',
        timestamp: new Date(),
        sentiment: analyzeSentiment(messageText)
    };

    appState.currentConversation.messages.push(userMessage);

    elements.messageInput.value = '';

    if (elements.charCount) {
        elements.charCount.textContent = '0/1000';
    }

    renderMessage(userMessage);
    showTypingIndicator();

    const startTime = Date.now();

    try {
        const botResponse = await generateAIResponse(messageText);
        const responseTime = Date.now() - startTime;

        const botMessage = {
            id: Date.now() + 1,
            text: botResponse,
            sender: 'bot',
            timestamp: new Date(),
            sentiment: 'positive',
            responseTime: responseTime
        };

        appState.currentConversation.messages.push(botMessage);
        appState.analytics.responseTimes.push(responseTime);
        appState.analytics.apiCalls.successful++;

        hideTypingIndicator();
        renderMessage(botMessage);

        updateAnalytics();
        updateSessionInfo();
        updateTopics(messageText);
        updateApiStatus(true);

    } catch (error) {
        console.error('Error generating AI response:', error);
        appState.analytics.apiCalls.failed++;

        const errorResponse =
            error.userMessage || getRandomFallbackResponse(error);

        const botMessage = {
            id: Date.now() + 1,
            text: errorResponse,
            sender: 'bot',
            timestamp: new Date(),
            sentiment: 'neutral',
            isError: true
        };

        appState.currentConversation.messages.push(botMessage);

        hideTypingIndicator();
        renderMessage(botMessage);
        updateApiStatus(false);
        updateAnalytics();
        updateSessionInfo();

    } finally {
        if (elements.sendBtn) {
            elements.sendBtn.disabled = false;
        }

        appState.isBusy = false;
        scrollToBottom();
    }
}

// ============================================================
// RESTORE CONVERSATION
// ============================================================

async function restoreConversation() {
    if (!conversationId) return;

    appState.isBusy = true;

    try {
        const response = await fetch(
            `${GEMINI_CONFIG.conversationsEndpoint}/${conversationId}/messages`
        );

        if (!response.ok) return;

        const data = await response.json();

        (data.messages || []).forEach(stored => {
            const isUser = stored.role === 'user';

            const message = {
                id: stored.id,
                text: stored.content,
                sender: isUser ? 'user' : 'bot',
                timestamp: new Date(stored.created_at),
                sentiment: isUser
                    ? analyzeSentiment(stored.content)
                    : 'positive',
                restored: true
            };

            appState.currentConversation.messages.push(message);
            renderMessage(message);
        });

        updateAnalytics();
        updateSessionInfo();

    } catch (error) {
        console.warn('Could not restore the previous conversation:', error);
    } finally {
        appState.isBusy = false;
    }
}

// ============================================================
// AI API COMMUNICATION
// ============================================================

async function generateAIResponse(userMessage) {
    try {
        const response = await fetch(GEMINI_CONFIG.apiEndpoint, {
            method: 'POST',

            headers: {
                'Content-Type': 'application/json'
            },

            body: JSON.stringify({
                message: userMessage,
                temperature: GEMINI_CONFIG.temperature,
                conversation_id: conversationId,
                use_history: appState.settings.contextMemory
            })
        });

        if (!response.ok) {
            const failure = new Error(
                `HTTP ${response.status}: ${response.statusText}`
            );

            failure.status = response.status;

            try {
                const errorBody = await response.json();

                if (errorBody?.error?.message) {
                    failure.userMessage = errorBody.error.message;
                }
            } catch (parseError) {
                // Use a generic message if the error body is not JSON.
            }

            throw failure;
        }

        const data = await response.json();
        return data.reply;

    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

function getRandomFallbackResponse(error) {
    let responses;

    if (
        error.message.includes('fetch') ||
        error.message.includes('network')
    ) {
        responses = fallbackResponses.networkError;
    } else if (
        error.message.includes('API') ||
        error.message.includes('HTTP')
    ) {
        responses = fallbackResponses.apiError;
    } else {
        responses = fallbackResponses.genericError;
    }

    return responses[Math.floor(Math.random() * responses.length)];
}

// ============================================================
// SENTIMENT ANALYSIS
// ============================================================

function analyzeSentiment(text) {
    const lowerText = String(text).toLowerCase();

    const positiveWords = [
        'good', 'great', 'awesome', 'excellent', 'amazing',
        'wonderful', 'love', 'like', 'happy', 'fantastic',
        'perfect', 'thank', 'thanks'
    ];

    const negativeWords = [
        'bad', 'terrible', 'awful', 'horrible', 'hate',
        'dislike', 'sad', 'angry', 'frustrated',
        'disappointed', 'problem', 'issue', 'wrong'
    ];

    let positiveCount = 0;
    let negativeCount = 0;

    positiveWords.forEach(word => {
        if (lowerText.includes(word)) positiveCount++;
    });

    negativeWords.forEach(word => {
        if (lowerText.includes(word)) negativeCount++;
    });

    if (positiveCount > negativeCount) return 'positive';
    if (negativeCount > positiveCount) return 'negative';

    return 'neutral';
}

// ============================================================
// SAFE MARKDOWN RENDERER
// ============================================================

// Supported formatting:
// **bold**, __bold__, *italic*, _italic_, `inline code`
// # headings, bullet lists, numbered lists, Markdown tables,
// fenced code blocks, blockquotes and horizontal rules.
//
// AI text is never directly inserted as executable HTML.

function appendMarkdownInline(parent, value) {
    const text = String(value ?? '');

    const pattern =
        /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|(?<!\*)\*[^*\n]+\*(?!\*)|(?<!_)_[^_\n]+_(?!_))/g;

    let position = 0;

    for (const match of text.matchAll(pattern)) {
        if (match.index > position) {
            parent.appendChild(
                document.createTextNode(
                    text.slice(position, match.index)
                )
            );
        }

        const token = match[0];
        const strong =
            token.startsWith('**') || token.startsWith('__');
        const code = token.startsWith('`');

        const element = document.createElement(
            code ? 'code' : strong ? 'strong' : 'em'
        );

        element.textContent = token.slice(
            strong ? 2 : 1,
            strong ? -2 : -1
        );

        parent.appendChild(element);
        position = match.index + token.length;
    }

    if (position < text.length) {
        parent.appendChild(
            document.createTextNode(text.slice(position))
        );
    }
}

function markdownTableCells(line) {
    return String(line)
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map(cell => cell.trim());
}

function isMarkdownTableSeparator(line, expectedColumns) {
    const cells = markdownTableCells(line);

    return (
        cells.length === expectedColumns &&
        cells.every(cell => /^:?-{3,}:?$/.test(cell))
    );
}

function renderSafeMarkdown(container, markdown) {
    const lines = String(markdown ?? '')
        .replace(/\r\n?/g, '\n')
        .split('\n');

    let list = null;
    let index = 0;

    while (index < lines.length) {
        const line = lines[index];
        const trimmed = line.trim();

        // ----------------------------------------------------
        // FENCED CODE BLOCKS
        // ----------------------------------------------------

        if (/^\s*```/.test(line)) {
            list = null;

            const codeLines = [];
            index++;

            while (
                index < lines.length &&
                !/^\s*```\s*$/.test(lines[index])
            ) {
                codeLines.push(lines[index]);
                index++;
            }

            if (index < lines.length) {
                index++;
            }

            const pre = document.createElement('pre');
            const code = document.createElement('code');

            code.textContent = codeLines.join('\n');

            pre.style.cssText = [
                'overflow-x:auto',
                'padding:12px',
                'border-radius:8px',
                'background:rgba(128,128,128,0.12)',
                'white-space:pre',
                'margin:10px 0'
            ].join(';');

            pre.appendChild(code);
            container.appendChild(pre);

            continue;
        }

        // ----------------------------------------------------
        // EMPTY LINES
        // ----------------------------------------------------

        if (!trimmed) {
            list = null;
            index++;
            continue;
        }

        // ----------------------------------------------------
        // MARKDOWN TABLES
        // ----------------------------------------------------

        const headers = markdownTableCells(line);

        if (
            line.includes('|') &&
            index + 1 < lines.length &&
            isMarkdownTableSeparator(
                lines[index + 1],
                headers.length
            )
        ) {
            list = null;

            const wrapper = document.createElement('div');

            wrapper.style.cssText = [
                'overflow-x:auto',
                'max-width:100%',
                'margin:10px 0'
            ].join(';');

            const table = document.createElement('table');

            table.style.cssText = [
                'border-collapse:collapse',
                'width:100%',
                'font-size:0.9em'
            ].join(';');

            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');

            headers.forEach(header => {
                const th = document.createElement('th');

                th.style.cssText = [
                    'border:1px solid #8886',
                    'padding:8px',
                    'text-align:left',
                    'background:rgba(128,128,128,0.12)',
                    'font-weight:700'
                ].join(';');

                appendMarkdownInline(th, header);
                headerRow.appendChild(th);
            });

            thead.appendChild(headerRow);
            table.appendChild(thead);

            const tbody = document.createElement('tbody');

            index += 2;

            while (
                index < lines.length &&
                lines[index].trim() &&
                lines[index].includes('|')
            ) {
                const cells = markdownTableCells(lines[index]);
                const row = document.createElement('tr');

                headers.forEach((header, columnIndex) => {
                    const td = document.createElement('td');

                    td.style.cssText = [
                        'border:1px solid #8886',
                        'padding:8px',
                        'vertical-align:top'
                    ].join(';');

                    appendMarkdownInline(
                        td,
                        cells[columnIndex] ?? ''
                    );

                    row.appendChild(td);
                });

                tbody.appendChild(row);
                index++;
            }

            table.appendChild(tbody);
            wrapper.appendChild(table);
            container.appendChild(wrapper);

            continue;
        }

        // ----------------------------------------------------
        // HEADINGS
        // ----------------------------------------------------

        const heading = line.match(/^\s*(#{1,6})\s+(.+)$/);

        if (heading) {
            list = null;

            const level = heading[1].length;
            const element = document.createElement(`h${level}`);

            element.style.cssText = [
                'font-weight:700',
                'margin:0.7em 0 0.35em',
                `font-size:${
                    level === 1 ? '1.35em' :
                    level === 2 ? '1.2em' :
                    level === 3 ? '1.1em' : '1em'
                }`
            ].join(';');

            appendMarkdownInline(element, heading[2]);
            container.appendChild(element);

            index++;
            continue;
        }

        // ----------------------------------------------------
        // HORIZONTAL RULES
        // ----------------------------------------------------

        if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
            list = null;

            const hr = document.createElement('hr');

            hr.style.cssText =
                'border:0;border-top:1px solid #8886;margin:12px 0';

            container.appendChild(hr);

            index++;
            continue;
        }

        // ----------------------------------------------------
        // BLOCKQUOTES
        // ----------------------------------------------------

        const quote = line.match(/^\s*>\s?(.*)$/);

        if (quote) {
            list = null;

            const blockquote = document.createElement('blockquote');

            blockquote.style.cssText = [
                'border-left:3px solid #888',
                'padding-left:12px',
                'margin:8px 0',
                'opacity:0.9'
            ].join(';');

            appendMarkdownInline(blockquote, quote[1]);
            container.appendChild(blockquote);

            index++;
            continue;
        }

        // ----------------------------------------------------
        // BULLET LISTS AND NUMBERED LISTS
        // ----------------------------------------------------

        const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
        const numbered = line.match(/^\s*(\d+)[.)]\s+(.+)$/);

        if (bullet || numbered) {
            const type = bullet ? 'ul' : 'ol';

            if (!list || list.tagName.toLowerCase() !== type) {
                list = document.createElement(type);

                list.style.cssText =
                    'padding-left:1.6em;margin:0.4em 0';

                if (numbered) {
                    list.start = Number(numbered[1]);
                }

                container.appendChild(list);
            }

            const item = document.createElement('li');

            item.style.marginBottom = '0.3em';

            appendMarkdownInline(
                item,
                bullet ? bullet[1] : numbered[2]
            );

            list.appendChild(item);

            index++;
            continue;
        }

        // ----------------------------------------------------
        // NORMAL TEXT
        // ----------------------------------------------------

        list = null;

        const paragraph = document.createElement('div');

        paragraph.style.lineHeight = '1.6';

        appendMarkdownInline(paragraph, line);
        container.appendChild(paragraph);

        index++;
    }
}

// Expose the function for browser Console verification.
window.renderSafeMarkdown = renderSafeMarkdown;

console.info('Chatbot Markdown renderer v2 loaded');

// ============================================================
// MESSAGE RENDERING
// ============================================================

function renderMessage(message) {
    if (!elements.chatMessages) return;

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.sender}-message`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';

    avatar.textContent = message.sender === 'bot' ? '🤖' : '👤';

    const content = document.createElement('div');
    content.className = 'message-content';

    const textDiv = document.createElement('div');
    textDiv.className = 'message-text';

    // Apply Markdown formatting only to AI messages.
    if (message.sender === 'bot' && !message.isError) {
        renderSafeMarkdown(textDiv, message.text);
    } else {
        textDiv.textContent = message.text;
    }

    if (message.isError) {
        textDiv.classList.add('error-message');
    }

    const timeDiv = document.createElement('div');
    timeDiv.className = 'message-time';
    timeDiv.textContent = formatTime(message.timestamp);

    content.appendChild(textDiv);

    if (appState.settings.showTimestamps) {
        content.appendChild(timeDiv);
    }

    if (
        appState.settings.enableSentiment &&
        message.sender === 'user' &&
        message.sentiment
    ) {
        const sentimentDiv = document.createElement('div');

        sentimentDiv.className =
            `sentiment-indicator ${message.sentiment}`;

        sentimentDiv.textContent =
            `${getSentimentIcon(message.sentiment)} ${message.sentiment}`;

        content.appendChild(sentimentDiv);
    }

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(content);

    elements.chatMessages.appendChild(messageDiv);

    scrollToBottom();
}

function getSentimentIcon(sentiment) {
    switch (sentiment) {
        case 'positive':
            return '😊';
        case 'negative':
            return '😔';
        default:
            return '😐';
    }
}

function showTypingIndicator() {
    if (elements.typingIndicator) {
        elements.typingIndicator.classList.remove('hidden');
        scrollToBottom();
    }
}

function hideTypingIndicator() {
    if (elements.typingIndicator) {
        elements.typingIndicator.classList.add('hidden');
    }
}

function scrollToBottom() {
    if (elements.chatMessages) {
        elements.chatMessages.scrollTop =
            elements.chatMessages.scrollHeight;
    }
}

function formatTime(date) {
    const timestamp = date instanceof Date
        ? date
        : new Date(date);

    return timestamp.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

// ============================================================
// NAVIGATION
// ============================================================

function switchSection(sectionName) {
    elements.navItems.forEach(nav => {
        nav.classList.remove('active');

        if (nav.getAttribute('data-section') === sectionName) {
            nav.classList.add('active');
        }
    });

    elements.mobileNavItems.forEach(nav => {
        nav.classList.remove('active');

        if (nav.getAttribute('data-section') === sectionName) {
            nav.classList.add('active');
        }
    });

    elements.contentSections.forEach(section => {
        section.classList.remove('active');

        if (section.id === `${sectionName}-section`) {
            section.classList.add('active');
        }
    });

    appState.currentSection = sectionName;

    if (sectionName === 'analytics') {
        setTimeout(initializeCharts, 100);
    }
}

// ============================================================
// THEME
// ============================================================

function toggleTheme() {
    const currentTheme =
        document.documentElement.getAttribute('data-color-scheme');

    const newTheme =
        currentTheme === 'dark' ? 'light' : 'dark';

    document.documentElement.setAttribute(
        'data-color-scheme',
        newTheme
    );

    if (elements.themeToggle) {
        const icon = elements.themeToggle.querySelector('i');

        if (icon) {
            icon.className = newTheme === 'dark'
                ? 'fas fa-sun'
                : 'fas fa-moon';
        }
    }

    appState.settings.theme = newTheme;

    if (elements.themeSelect) {
        elements.themeSelect.value = newTheme;
    }
}

function applyTheme() {
    const theme = appState.settings.theme;

    if (theme === 'auto') {
        document.documentElement.removeAttribute(
            'data-color-scheme'
        );
    } else {
        document.documentElement.setAttribute(
            'data-color-scheme',
            theme
        );
    }

    if (elements.themeToggle) {
        const icon = elements.themeToggle.querySelector('i');

        if (icon) {
            icon.className = theme === 'dark'
                ? 'fas fa-sun'
                : 'fas fa-moon';
        }
    }
}

// ============================================================
// ANALYTICS
// ============================================================

function updateAnalytics() {
    appState.analytics.totalMessages =
        appState.currentConversation.messages.length;

    appState.analytics.sentimentCounts = {
        positive: 0,
        negative: 0,
        neutral: 0
    };

    appState.currentConversation.messages.forEach(message => {
        if (
            message.sentiment &&
            appState.analytics.sentimentCounts[message.sentiment] !== undefined
        ) {
            appState.analytics.sentimentCounts[message.sentiment]++;
        }
    });

    const totalConversations =
        document.getElementById('totalConversations');

    const totalMessages =
        document.getElementById('totalMessages');

    const avgResponseTime =
        document.getElementById('avgResponseTime');

    const apiSuccessRate =
        document.getElementById('apiSuccessRate');

    if (totalConversations) {
        totalConversations.textContent =
            appState.analytics.totalConversations;
    }

    if (totalMessages) {
        totalMessages.textContent =
            appState.analytics.totalMessages;
    }

    if (
        avgResponseTime &&
        appState.analytics.responseTimes.length > 0
    ) {
        const times = appState.analytics.responseTimes;

        const avg =
            times.reduce((a, b) => a + b, 0) / times.length;

        avgResponseTime.textContent =
            `${(avg / 1000).toFixed(1)}s`;
    }

    if (apiSuccessRate) {
        const successful = appState.analytics.apiCalls.successful;
        const failed = appState.analytics.apiCalls.failed;
        const total = successful + failed;

        const rate = total > 0
            ? (successful / total * 100).toFixed(0)
            : 100;

        apiSuccessRate.textContent = `${rate}%`;
    }

    updateCharts();
}

// ============================================================
// CHARTS
// ============================================================

function initializeCharts() {
    const sentimentCtx = document.getElementById('sentimentChart');

    if (sentimentCtx && !sentimentCtx.chartInstance && typeof Chart !== 'undefined') {
        const sentimentData = appState.analytics.sentimentCounts;

        sentimentCtx.chartInstance = new Chart(sentimentCtx, {
            type: 'doughnut',

            data: {
                labels: ['Positive', 'Negative', 'Neutral'],

                datasets: [{
                    data: [
                        sentimentData.positive,
                        sentimentData.negative,
                        sentimentData.neutral
                    ],

                    backgroundColor: [
                        '#1FB8CD',
                        '#B4413C',
                        '#5D878F'
                    ],

                    borderWidth: 0
                }]
            },

            options: {
                responsive: true,
                maintainAspectRatio: true,

                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }

    const responseTimeCtx =
        document.getElementById('responseTimeChart');

    if (
        responseTimeCtx &&
        !responseTimeCtx.chartInstance &&
        typeof Chart !== 'undefined'
    ) {
        const responseTimes =
            appState.analytics.responseTimes
                .slice(-10)
                .map(time => time / 1000);

        const labels = Array.from(
            { length: responseTimes.length },
            (_, i) => `Msg ${i + 1}`
        );

        responseTimeCtx.chartInstance = new Chart(responseTimeCtx, {
            type: 'line',

            data: {
                labels: labels,

                datasets: [{
                    label: 'Response Time (seconds)',
                    data: responseTimes,
                    backgroundColor: '#FFC185',
                    borderColor: '#1FB8CD',
                    borderWidth: 2,
                    fill: false,
                    tension: 0.4
                }]
            },

            options: {
                responsive: true,
                maintainAspectRatio: true,

                plugins: {
                    legend: {
                        display: false
                    }
                },

                scales: {
                    y: {
                        beginAtZero: true,

                        title: {
                            display: true,
                            text: 'Seconds'
                        }
                    }
                }
            }
        });
    }
}

function updateCharts() {
    const sentimentCtx = document.getElementById('sentimentChart');

    if (sentimentCtx && sentimentCtx.chartInstance) {
        const sentimentData = appState.analytics.sentimentCounts;

        sentimentCtx.chartInstance.data.datasets[0].data = [
            sentimentData.positive,
            sentimentData.negative,
            sentimentData.neutral
        ];

        sentimentCtx.chartInstance.update();
    }

    const responseTimeCtx =
        document.getElementById('responseTimeChart');

    if (responseTimeCtx && responseTimeCtx.chartInstance) {
        const responseTimes =
            appState.analytics.responseTimes
                .slice(-10)
                .map(time => time / 1000);

        const labels = Array.from(
            { length: responseTimes.length },
            (_, i) => `Msg ${i + 1}`
        );

        responseTimeCtx.chartInstance.data.labels = labels;

        responseTimeCtx.chartInstance.data.datasets[0].data =
            responseTimes;

        responseTimeCtx.chartInstance.update();
    }
}

// ============================================================
// SESSION INFORMATION
// ============================================================

function updateSessionInfo() {
    if (elements.sessionMessages) {
        elements.sessionMessages.textContent =
            appState.currentConversation.messages.length;
    }

    if (elements.apiCalls) {
        elements.apiCalls.textContent =
            appState.analytics.apiCalls.successful +
            appState.analytics.apiCalls.failed;
    }

    const userMessages =
        appState.currentConversation.messages
            .filter(message => message.sender === 'user')
            .slice(-3);

    if (userMessages.length > 0) {
        const sentiments = userMessages.map(message => message.sentiment);

        const mostCommonSentiment = sentiments.reduce(
            (a, b, i, arr) =>
                arr.filter(v => v === a).length >=
                arr.filter(v => v === b).length
                    ? a
                    : b
        );

        if (elements.sessionSentiment) {
            elements.sessionSentiment.textContent =
                mostCommonSentiment.charAt(0).toUpperCase() +
                mostCommonSentiment.slice(1);

            elements.sessionSentiment.className =
                `stat-value sentiment ${mostCommonSentiment}`;
        }
    }
}

// ============================================================
// TOPIC TRACKING
// ============================================================

function updateTopics(message) {
    const topics = extractTopics(message);

    topics.forEach(topic => {
        if (!appState.currentConversation.topics.includes(topic)) {
            appState.currentConversation.topics.push(topic);

            if (elements.recentTopics) {
                const topicDiv = document.createElement('div');

                topicDiv.className = 'topic-item';
                topicDiv.textContent = topic;

                elements.recentTopics.appendChild(topicDiv);

                if (elements.recentTopics.children.length > 5) {
                    elements.recentTopics.removeChild(
                        elements.recentTopics.firstChild
                    );
                }
            }
        }
    });
}

function extractTopics(text) {
    const keywords = [
        'ai',
        'artificial intelligence',
        'machine learning',
        'technology',
        'coding',
        'programming',
        'help',
        'question',
        'problem',
        'explain'
    ];

    const topics = [];

    keywords.forEach(keyword => {
        if (text.toLowerCase().includes(keyword)) {
            topics.push(
                keyword.charAt(0).toUpperCase() +
                keyword.slice(1)
            );
        }
    });

    return topics.length > 0 ? topics : ['General'];
}

// ============================================================
// API STATUS
// ============================================================

function updateApiStatus(isOnline) {
    const statusIndicators =
        document.querySelectorAll('.status-indicator');

    statusIndicators.forEach(indicator => {
        if (isOnline) {
            indicator.classList.add('online');
            indicator.classList.remove('offline');
        } else {
            indicator.classList.remove('online');
            indicator.classList.add('offline');
        }
    });

    if (elements.apiStatus) {
        const statusText =
            elements.apiStatus.querySelector('span:last-child');

        if (statusText) {
            statusText.textContent =
                isOnline ? 'AI Online' : 'AI Offline';
        }
    }
}

async function testApiConnection() {
    try {
        const response = await fetch(GEMINI_CONFIG.healthEndpoint);

        const health = response.ok
            ? await response.json()
            : null;

        updateApiStatus(
            Boolean(
                health &&
                health.ai &&
                health.ai.configured
            )
        );

    } catch (error) {
        updateApiStatus(false);
    }
}

// ============================================================
// SESSION TIMER
// ============================================================

function startSessionTimer() {
    setInterval(() => {
        const duration = Math.floor(
            (
                Date.now() -
                appState.currentConversation.startTime
            ) / 60000
        );

        if (elements.sessionDuration) {
            elements.sessionDuration.textContent = `${duration}m`;
        }
    }, 60000);
}

// ============================================================
// RERENDER CHAT
// ============================================================

function rerenderMessages() {
    if (!elements.chatMessages) return;

    elements.chatMessages.replaceChildren();

    appState.currentConversation.messages.forEach(message => {
        renderMessage(message);
    });

    scrollToBottom();
}

// ============================================================
// EXPORT CHAT
// ============================================================

function exportChatHistory() {
    const chatData = {
        conversation: appState.currentConversation,
        conversationId: conversationId,
        analytics: appState.analytics,
        exportDate: new Date().toISOString(),
        messageCount: appState.currentConversation.messages.length
    };

    const dataStr = JSON.stringify(chatData, null, 2);

    const dataBlob = new Blob(
        [dataStr],
        { type: 'application/json' }
    );

    const link = document.createElement('a');
    const url = URL.createObjectURL(dataBlob);

    link.href = url;

    link.download =
        `chat-history-${new Date().toISOString().split('T')[0]}.json`;

    link.click();

    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ============================================================
// CLEAR CHAT
// ============================================================

async function clearChatHistory() {
    if (!confirm(
        'Are you sure you want to clear the chat history? This also deletes the conversation from the server and cannot be undone.'
    )) {
        return;
    }

    try {
        const response = await fetch(
            `${GEMINI_CONFIG.conversationsEndpoint}/${conversationId}`,
            {
                method: 'DELETE'
            }
        );

        if (!response.ok && response.status !== 404) {
            throw new Error(`HTTP ${response.status}`);
        }

    } catch (error) {
        alert(
            'Could not delete the conversation on the server, so nothing was cleared. Please try again.'
        );

        return;
    }

    conversationId = generateUuidV4();
    saveConversationId(conversationId);

    appState.currentConversation.messages = [];
    appState.currentConversation.topics = [];
    appState.currentConversation.startTime = Date.now();

    appState.analytics = {
        totalConversations: 1,
        totalMessages: 1,
        responseTimes: [],

        apiCalls: {
            successful: 0,
            failed: 0
        },

        sentimentCounts: {
            positive: 1,
            negative: 0,
            neutral: 0
        }
    };

    if (elements.chatMessages) {
        elements.chatMessages.replaceChildren();
    }

    if (elements.recentTopics) {
        elements.recentTopics.replaceChildren();

        const generalTopic = document.createElement('div');
        generalTopic.className = 'topic-item';
        generalTopic.textContent = 'General';

        elements.recentTopics.appendChild(generalTopic);
    }

    initializeApp();
    updateAnalytics();
    updateSessionInfo();
}
