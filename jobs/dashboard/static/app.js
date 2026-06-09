document.addEventListener('DOMContentLoaded', () => {
    const chatMessages = document.getElementById('chat-messages');
    const userInput = document.getElementById('user-input');
    const sendButton = document.getElementById('send-button');

    const ws_scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const ws_path = ws_scheme + '://' + window.location.host + '/ws';
    const socket = new WebSocket(ws_path);

    socket.onopen = function(event) {
        console.log("WebSocket connected!");
        addMessageToChat('System', 'Connected to Watson.', false);
    };

    socket.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.type === 'bot_message') {
            addMessageToChat('Watson', data.message, false);
        } else if (data.type === 'skill_result') {
            addMessageToChat('Skill Result', data.result, false);
        } else if (data.type === 'user_message_ack') {
            // Server acknowledges user message, no need to display again if already shown client-side
        }
    };

    socket.onclose = function(event) {
        console.log("WebSocket disconnected!");
        addMessageToChat('System', 'Disconnected from Watson. Please refresh.', false);
    };

    socket.onerror = function(error) {
        console.error("WebSocket Error: ", error);
        addMessageToChat('System', 'WebSocket error occurred.', false);
    };

    sendButton.addEventListener('click', sendMessage);
    userInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            sendMessage();
        }
    });

    function sendMessage() {
        const message = userInput.value.trim();
        if (message) {
            socket.send(JSON.stringify({ type: 'user_message', message: message }));
            addMessageToChat('You', message, true); // Display user's message immediately
            userInput.value = '';
        }
    }

    function addMessageToChat(sender, message, isUser) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('chat-message');
        if (isUser) {
            messageDiv.classList.add('user');
        } else {
            messageDiv.classList.add('bot');
        }

        const senderSpan = document.createElement('span');
        senderSpan.classList.add('sender');
        senderSpan.textContent = sender + ': ';
        messageDiv.appendChild(senderSpan);

        const contentElement = document.createElement('span');
        contentElement.classList.add('message-content');

        // Check if the message is a base64 image data URL
        if (message && typeof message === 'string' && message.startsWith('data:image/')) {
            const imgElement = document.createElement('img');
            imgElement.src = message;
            imgElement.alt = 'Skill Result Image';
            imgElement.style.maxWidth = '100%'; // Ensure image fits within the chat bubble
            imgElement.style.height = 'auto';   // Maintain aspect ratio
            imgElement.style.display = 'block'; // Prevent extra space below the image
            contentElement.appendChild(imgElement);
        } else {
            contentElement.textContent = message;
        }

        messageDiv.appendChild(contentElement);
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
});
