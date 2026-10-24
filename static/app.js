// The API key and model name live ONLY on the server (environment variables).
// Never put secrets in this file: everything in static/ is public.
const GEMINI_CONFIG = {
    apiEndpoint: '/api/v1/chat',
    healthEndpoint: '/api/v1/health',
    conversationsEndpoint: '/api/v1/conversations',
    temperature: 0.7
};

// Conversation identity. The browser creates a random UUID and keeps it in localStorage;
// the server stores the conversation under that id so it survives page refreshes and
// server restarts. The id is the only "key" to the conversation (there are no accounts),
// so treat it like a password: it is never shown or logged in full.
// If localStorage is unavailable (private mode, blocked storage) the id lasts only
// until the page is closed.
const CONVERSATION_STORAGE_KEY = 'chatbot.conversation_id';
const UUID_V4_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
let conversationId = null;

function generateUuidV4() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    // randomUUID needs a secure context (https or localhost); build one by hand otherwise.
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function saveConversationId(id) {
    try {
        window.localStorage.setItem(CONVERSATION_STORAGE_KEY, id);
    } catch (error) {
        // Storage unavailable: the conversation just won't survive a refresh.
    }
}

function loadOrCreateConversationId() {
    try {
        const stored = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
        if (stored && UUID_V4_PATTERN.test(stored)) {
            return stored;
        }
    } catch (error) {
        // Storage unavailable: fall through and create an in-memory id.
    }
    const created = generateUuidV4();
    saveConversationId(created);
    return created;
}

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
        "Oops! I'm experiencing some technical difficulties. Let me try to help you with a basic response.",
        "I'm temporarily offline for maintenance. Please retry your question shortly."
    ],
    networkError: [
        "It seems there's a network issue. Please check your connection and try again.",
        "I can't reach my AI servers at the moment. Please try again when your connection is stable."
    ],
    genericError: [
        "Something unexpected happened. Let me try to process your request again.",
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

document.addEventListener('DOMContentLoaded', function() {
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

function setupEventListeners() {
    if (elements.messageInput) {
        elements.messageInput.addEventListener('input', handleInputChange);
        elements.messageInput.addEventListener('keypress', handleKeyPress);
    }

    if (elements.sendBtn) {
        elements.sendBtn.addEventListener('click', sendMessage);
    }

    elements.quickReplies.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const message = e.target.getAttribute('data-message');
            if (elements.messageInput) {
                elements.messageInput.value = message;
                sendMessage();
            }
        });
    });

    elements.navItems.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const section = e.currentTarget.getAttribute('data-section');
            switchSection(section);
        });
    });

    elements.mobileNavItems.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const section = e.currentTarget.getAttribute('data-section');
            switchSection(section);
        });
    });

    if (elements.themeToggle) {
        elements.themeToggle.addEventListener('click', toggleTheme);
    }

    if (elements.themeSelect) {
        elements.themeSelect.addEventListener('change', (e) => {
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
        languageSelect.addEventListener('change', (e) => {
            appState.settings.language = e.target.value;
        });
    }

    if (temperatureSlider && temperatureValue) {
        temperatureSlider.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value);
            appState.settings.temperature = value;
            temperatureValue.textContent = value;
            GEMINI_CONFIG.temperature = value;
        });
    }

    if (showTimestamps) {
        showTimestamps.addEventListener('change', (e) => {
            appState.settings.showTimestamps = e.target.checked;
            rerenderMessages();
        });
    }

    if (enableSentiment) {
        enableSentiment.addEventListener('change', (e) => {
            appState.settings.enableSentiment = e.target.checked;
            rerenderMessages();
        });
    }

    if (contextMemory) {
        contextMemory.addEventListener('change', (e) => {
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
    if (elements.charCount) {
        elements.charCount.textContent = `${length}/1000`;
        if (length >= 900) {
            elements.charCount.style.color = 'var(--color-warning)';
        } else if (length >= 950) {
            elements.charCount.style.color = 'var(--color-error)';
        } else {
            elements.charCount.style.color = 'var(--color-text-secondary)';
        }
    }
}

function handleKeyPress(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

async function sendMessage() {
    if (!elements.messageInput) return;

    const messageText = elements.messageInput.value.trim();
    if (!messageText) return;

    // One request at a time: replies are stored in order, and Enter can be pressed
    // while the send button is disabled.
    if (appState.isBusy) return;
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

        const errorResponse = error.userMessage || getRandomFallbackResponse(error);
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
    }

    if (elements.sendBtn) {
        elements.sendBtn.disabled = false;
    }
    appState.isBusy = false;

    scrollToBottom();
}

// Reload this browser's conversation from the server after a refresh or restart.
// The welcome message stays first; stored messages follow it.
async function restoreConversation() {
    if (!conversationId) return;
    appState.isBusy = true;
    try {
        const response = await fetch(
            `${GEMINI_CONFIG.conversationsEndpoint}/${conversationId}/messages`
        );
        if (!response.ok) return; // 404 = a brand-new conversation, nothing to restore
        const data = await response.json();
        (data.messages || []).forEach(stored => {
            const isUser = stored.role === 'user';
            const message = {
                id: stored.id,
                text: stored.content,
                sender: isUser ? 'user' : 'bot',
                timestamp: new Date(stored.created_at),
                sentiment: isUser ? analyzeSentiment(stored.content) : 'positive',
                restored: true
            };
            appState.currentConversation.messages.push(message);
            renderMessage(message);
        });
        updateAnalytics();
        updateSessionInfo();
    } catch (error) {
        // Server unreachable: start with an empty view; sending will report the problem.
        console.warn('Could not restore the previous conversation:', error);
    } finally {
        appState.isBusy = false;
    }
}

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
            // The server returns a real error status plus a user-safe message.
            const failure = new Error(`HTTP ${response.status}: ${response.statusText}`);
            failure.status = response.status;
            try {
                const errorBody = await response.json();
                if (errorBody && errorBody.error && errorBody.error.message) {
                    failure.userMessage = errorBody.error.message;
                }
            } catch (parseError) {
                // Non-JSON error body: fall back to the generic messages.
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
    if (error.message.includes('fetch') || error.message.includes('network')) {
        responses = fallbackResponses.networkError;
    } else if (error.message.includes('API') || error.message.includes('HTTP')) {
        responses = fallbackResponses.apiError;
    } else {
        responses = fallbackResponses.genericError;
    }
    return responses[Math.floor(Math.random() * responses.length)];
}

function analyzeSentiment(text) {
    const lowerText = text.toLowerCase();
    const positiveWords = ['good', 'great', 'awesome', 'excellent', 'amazing', 'wonderful', 'love', 'like', 'happy', 'fantastic', 'perfect', 'thank', 'thanks'];
    const negativeWords = ['bad', 'terrible', 'awful', 'horrible', 'hate', 'dislike', 'sad', 'angry', 'frustrated', 'disappointed', 'problem', 'issue', 'wrong'];

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

function renderMessage(message) {
    if (!elements.chatMessages) return;

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${message.sender}-message`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.innerHTML = message.sender === 'bot' ? '🤖' : '👤';

    const content = document.createElement('div');
    content.className = 'message-content';

    const textDiv = document.createElement('div');
    textDiv.className = 'message-text';
    textDiv.textContent = message.text;

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

    if (appState.settings.enableSentiment && message.sender === 'user' && message.sentiment) {
        const sentimentDiv = document.createElement('div');
        sentimentDiv.className = `sentiment-indicator ${message.sentiment}`;
        sentimentDiv.innerHTML = `${getSentimentIcon(message.sentiment)} ${message.sentiment}`;
        content.appendChild(sentimentDiv);
    }

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(content);
    elements.chatMessages.appendChild(messageDiv);

    scrollToBottom();
}

function getSentimentIcon(sentiment) {
    switch (sentiment) {
        case 'positive': return '😊';
        case 'negative': return '😔';
        default: return '😐';
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
        elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    }
}

function formatTime(date) {
    return date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

function switchSection(sectionName) {
    elements.navItems.forEach(nav => {
        nav.classList.remove('active');
        const navSection = nav.getAttribute('data-section');
        if (navSection === sectionName) {
            nav.classList.add('active');
        }
    });

    elements.mobileNavItems.forEach(nav => {
        nav.classList.remove('active');
        const navSection = nav.getAttribute('data-section');
        if (navSection === sectionName) {
            nav.classList.add('active');
        }
    });

    elements.contentSections.forEach(section => {
        section.classList.remove('active');
        const sectionId = section.id;
        if (sectionId === `${sectionName}-section`) {
            section.classList.add('active');
        }
    });

    appState.currentSection = sectionName;

    if (sectionName === 'analytics') {
        setTimeout(initializeCharts, 100);
    }
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-color-scheme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-color-scheme', newTheme);

    if (elements.themeToggle) {
        const icon = elements.themeToggle.querySelector('i');
        if (icon) {
            icon.className = newTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
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
        document.documentElement.removeAttribute('data-color-scheme');
    } else {
        document.documentElement.setAttribute('data-color-scheme', theme);
    }

    if (elements.themeToggle) {
        const icon = elements.themeToggle.querySelector('i');
        if (icon) {
            icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
        }
    }
}

function updateAnalytics() {
    appState.analytics.totalMessages = appState.currentConversation.messages.length;

    appState.analytics.sentimentCounts = {
        positive: 0,
        negative: 0,
        neutral: 0
    };

    appState.currentConversation.messages.forEach(message => {
        if (message.sentiment && appState.analytics.sentimentCounts[message.sentiment] !== undefined) {
            appState.analytics.sentimentCounts[message.sentiment]++;
        }
    });

    const totalConversations = document.getElementById('totalConversations');
    const totalMessages = document.getElementById('totalMessages');
    const avgResponseTime = document.getElementById('avgResponseTime');
    const apiSuccessRate = document.getElementById('apiSuccessRate');

    if (totalConversations) totalConversations.textContent = appState.analytics.totalConversations;
    if (totalMessages) totalMessages.textContent = appState.analytics.totalMessages;

    if (avgResponseTime && appState.analytics.responseTimes.length > 0) {
        const avg = appState.analytics.responseTimes.reduce((a, b) => a + b, 0) / appState.analytics.responseTimes.length;
        avgResponseTime.textContent = `${(avg / 1000).toFixed(1)}s`;
    }

    if (apiSuccessRate) {
        const total = appState.analytics.apiCalls.successful + appState.analytics.apiCalls.failed;
        const rate = total > 0 ? (appState.analytics.apiCalls.successful / total * 100).toFixed(0) : 100;
        apiSuccessRate.textContent = `${rate}%`;
    }

    updateCharts();
}

function initializeCharts() {
    const sentimentCtx = document.getElementById('sentimentChart');
    if (sentimentCtx && !sentimentCtx.chartInstance) {
        const sentimentData = appState.analytics.sentimentCounts;
        sentimentCtx.chartInstance = new Chart(sentimentCtx, {
            type: 'doughnut',
            data: {
                labels: ['Positive', 'Negative', 'Neutral'],
                datasets: [{
                    data: [sentimentData.positive, sentimentData.negative, sentimentData.neutral],
                    backgroundColor: ['#1FB8CD', '#B4413C', '#5D878F'],
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

    const responseTimeCtx = document.getElementById('responseTimeChart');
    if (responseTimeCtx && !responseTimeCtx.chartInstance) {
        const responseTimes = appState.analytics.responseTimes.slice(-10).map(time => time / 1000);
        const labels = Array.from({length: responseTimes.length}, (_, i) => `Msg ${i + 1}`);

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

    const responseTimeCtx = document.getElementById('responseTimeChart');
    if (responseTimeCtx && responseTimeCtx.chartInstance) {
        const responseTimes = appState.analytics.responseTimes.slice(-10).map(time => time / 1000);
        const labels = Array.from({length: responseTimes.length}, (_, i) => `Msg ${i + 1}`);

        responseTimeCtx.chartInstance.data.labels = labels;
        responseTimeCtx.chartInstance.data.datasets[0].data = responseTimes;
        responseTimeCtx.chartInstance.update();
    }
}

function updateSessionInfo() {
    if (elements.sessionMessages) {
        elements.sessionMessages.textContent = appState.currentConversation.messages.length;
    }

    if (elements.apiCalls) {
        elements.apiCalls.textContent = appState.analytics.apiCalls.successful + appState.analytics.apiCalls.failed;
    }

    const userMessages = appState.currentConversation.messages
        .filter(m => m.sender === 'user')
        .slice(-3);

    if (userMessages.length > 0) {
        const sentiments = userMessages.map(m => m.sentiment);
        const mostCommonSentiment = sentiments.reduce((a, b, i, arr) =>
            arr.filter(v => v === a).length >= arr.filter(v => v === b).length ? a : b
        );

        if (elements.sessionSentiment) {
            elements.sessionSentiment.textContent = mostCommonSentiment.charAt(0).toUpperCase() + mostCommonSentiment.slice(1);
            elements.sessionSentiment.className = `stat-value sentiment ${mostCommonSentiment}`;
        }
    }
}

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
                    elements.recentTopics.removeChild(elements.recentTopics.firstChild);
                }
            }
        }
    });
}

function extractTopics(text) {
    const keywords = ['ai', 'artificial intelligence', 'machine learning', 'technology', 'coding', 'programming', 'help', 'question', 'problem', 'explain'];
    const topics = [];

    keywords.forEach(keyword => {
        if (text.toLowerCase().includes(keyword)) {
            topics.push(keyword.charAt(0).toUpperCase() + keyword.slice(1));
        }
    });

    return topics.length > 0 ? topics : ['General'];
}

function updateApiStatus(isOnline) {
    const statusIndicators = document.querySelectorAll('.status-indicator');
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
        const statusText = elements.apiStatus.querySelector('span:last-child');
        if (statusText) {
            statusText.textContent = isOnline ? 'AI Online' : 'AI Offline';
        }
    }
}

async function testApiConnection() {
    // Asks the server whether the AI provider is configured. This does not call
    // Gemini, so it costs nothing; it cannot detect a Gemini outage.
    try {
        const response = await fetch(GEMINI_CONFIG.healthEndpoint);
        const health = response.ok ? await response.json() : null;
        updateApiStatus(Boolean(health && health.ai && health.ai.configured));
    } catch (error) {
        updateApiStatus(false);
    }
}

function startSessionTimer() {
    setInterval(() => {
        const duration = Math.floor((Date.now() - appState.currentConversation.startTime) / 60000);
        if (elements.sessionDuration) {
            elements.sessionDuration.textContent = `${duration}m`;
        }
    }, 60000);
}

function rerenderMessages() {
    if (!elements.chatMessages) return;

    elements.chatMessages.innerHTML = '';

    appState.currentConversation.messages.forEach(message => {
        renderMessage(message);
    });

    scrollToBottom();
}

function exportChatHistory() {
    const chatData = {
        conversation: appState.currentConversation,
        conversationId: conversationId,
        analytics: appState.analytics,
        exportDate: new Date().toISOString(),
        messageCount: appState.currentConversation.messages.length
    };

    const dataStr = JSON.stringify(chatData, null, 2);
    const dataBlob = new Blob([dataStr], {type: 'application/json'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(dataBlob);
    link.download = `chat-history-${new Date().toISOString().split('T')[0]}.json`;
    link.click();
}

async function clearChatHistory() {
    if (!confirm('Are you sure you want to clear the chat history? This also deletes the conversation from the server and cannot be undone.')) {
        return;
    }

    // Delete on the server first, so the chat cannot reappear after a refresh.
    try {
        const response = await fetch(`${GEMINI_CONFIG.conversationsEndpoint}/${conversationId}`, {
            method: 'DELETE'
        });
        if (!response.ok && response.status !== 404) {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        alert('Could not delete the conversation on the server, so nothing was cleared. Please try again.');
        return;
    }

    // Start a fresh, separate conversation.
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
        elements.chatMessages.innerHTML = '';
    }
    if (elements.recentTopics) {
        elements.recentTopics.innerHTML = '<div class="topic-item">General</div>';
    }

    initializeApp();
    updateAnalytics();
    updateSessionInfo();
}
