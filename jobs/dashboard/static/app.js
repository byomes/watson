document.addEventListener('DOMContentLoaded', () => {
    const chatInput = document.getElementById('chat-input');
    const sendButton = document.getElementById('send-button');
    const chatMessages = document.getElementById('chat-messages');

    function appendChatMessage(sender, message) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message-container');

        const senderElement = document.createElement('span');
        senderElement.classList.add('sender');
        senderElement.textContent = sender === 'user' ? 'You: ' : 'Watson: ';
        messageElement.appendChild(senderElement);

        const contentElement = document.createElement('span');
        contentElement.classList.add('message-content');
        
        if (message.startsWith('data:image/')) {
            const imgElement = document.createElement('img');
            imgElement.src = message;
            imgElement.alt = 'Generated Image';
            imgElement.style.maxWidth = '100%'; // Ensure image fits within chat bubble
            imgElement.style.height = 'auto';
            contentElement.appendChild(imgElement);
        } else {
            contentElement.textContent = message;
        }
        
        messageElement.appendChild(contentElement);
        chatMessages.appendChild(messageElement);
        chatMessages.scrollTop = chatMessages.scrollHeight; // Auto-scroll to latest message
    }

    function sendMessage() {
        const message = chatInput.value.trim();
        if (message === '') return;

        appendChatMessage('user', message);
        chatInput.value = '';

        const eventSource = new EventSource(`/chat?message=${encodeURIComponent(message)}`);

        eventSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            if (data.type === 'start') {
                appendChatMessage('watson', data.content);
            } else if (data.type === 'chunk') {
                const lastMessage = chatMessages.lastElementChild;
                const contentSpan = lastMessage ? lastMessage.querySelector('.message-content') : null;
                if (contentSpan) {
                    // If it's an image, don't append text chunks
                    if (!contentSpan.querySelector('img')) {
                        contentSpan.textContent += data.content;
                    }
                }
                chatMessages.scrollTop = chatMessages.scrollHeight;
            } else if (data.type === 'end') {
                eventSource.close();
            }
        };

        eventSource.onerror = function(err) {
            console.error('EventSource failed:', err);
            eventSource.close();
            appendChatMessage('watson', 'Error: Could not connect to the server or stream response.');
        };
    }

    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendMessage();
        }
    });

    sendButton.addEventListener('click', sendMessage);

    // Initial welcome message (optional)
    // appendChatMessage('watson', 'Hello! How can I help you today?');
});
