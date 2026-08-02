// navigator.js
// 
// Minimal public embed scaffold for Navigator AI.
//
// Usage:
// 1. Add this script to your HTML:
//    <script src="navigator.js" data-token="sess_YOUR_SESSION_TOKEN_HERE"></script>
//
// 2. The script will render a "Start a demo" button.
// 3. When clicked, it will call the Navigator AI backend to start a live demo
//    and automatically redirect the user to the generated meeting URL.

(function() {
    // Look up the script tag to read data attributes
    const scriptTag = document.currentScript || document.querySelector('script[src*="navigator.js"]');
    const token = scriptTag.getAttribute('data-token');
    const apiUrl = scriptTag.getAttribute('data-api-url') || 'http://localhost:8000'; // Change in prod

    if (!token) {
        console.error("Navigator AI: Missing data-token attribute on script tag.");
        return;
    }

    // Render a simple button
    const container = document.createElement('div');
    container.style.position = 'fixed';
    container.style.bottom = '20px';
    container.style.right = '20px';
    container.style.zIndex = '999999';

    const btn = document.createElement('button');
    btn.innerText = 'Start a demo';
    btn.style.padding = '12px 24px';
    btn.style.backgroundColor = '#0055FF';
    btn.style.color = '#FFF';
    btn.style.border = 'none';
    btn.style.borderRadius = '8px';
    btn.style.cursor = 'pointer';
    btn.style.fontWeight = 'bold';
    btn.style.boxShadow = '0 4px 6px rgba(0,0,0,0.1)';

    btn.onclick = async function() {
        btn.innerText = 'Starting…';
        btn.disabled = true;

        try {
            const res = await fetch(`${apiUrl}/v1/demos/start`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Token ${token}`
                },
                body: JSON.stringify({})
            });

            if (!res.ok) {
                const text = await res.text();
                throw new Error(`Failed to start demo: ${text}`);
            }

            const data = await res.json();
            if (data.meeting && data.meeting.url) {
                btn.innerText = 'Redirecting...';
                window.location.href = data.meeting.url;
            } else {
                throw new Error('No meeting URL returned.');
            }

        } catch (err) {
            console.error("Navigator AI Error:", err);
            alert("Failed to start the demo. Please try again later.");
            btn.innerText = 'Start a demo';
            btn.disabled = false;
        }
    };

    container.appendChild(btn);
    document.body.appendChild(container);
})();
