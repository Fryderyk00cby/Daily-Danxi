'use strict';

const imageQueue = [];
let activeImages = 0;
const imageObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
        if (entry.isIntersecting) {
            imageObserver.unobserve(entry.target);
            queueImage(entry.target);
        }
    }
}, {rootMargin: '250px'});

function decodeImageAttribute(text) {
    return text.replace(/&(?:amp|quot|apos|lt|gt|#39);/g, value =>
        ({'&amp;':'&','&quot;':'"','&apos;':"'",'&#39;':"'",'&lt;':'<','&gt;':'>'})[value]);
}

function supportedImage(raw) {
    try {
        const url = new URL(decodeImageAttribute(raw));
        if (!['https:', 'http:'].includes(url.protocol) || url.hostname !== 'image.fduhole.com' || url.username || url.password || url.port) return null;
        url.protocol = 'https:';
        url.hash = '';
        return url.href;
    } catch { return null; }
}

function queueImage(figure) {
    if (figure.dataset.queued === 'true') return;
    figure.dataset.queued = 'true';
    imageQueue.push(figure);
    pumpImages();
}

function pumpImages() {
    while (activeImages < 3 && imageQueue.length) {
        const figure = imageQueue.shift();
        if (!figure.isConnected) { figure.dataset.queued = 'false'; continue; }
        activeImages++;
        loadImage(figure).finally(() => {
            figure.dataset.queued = 'false';
            activeImages--;
            pumpImages();
        });
    }
}

async function loadImage(figure) {
    const status = figure.querySelector('figcaption');
    const retry = figure.querySelector('.image-retry');
    const image = figure.querySelector('img');
    status.textContent = '图片加载中…';
    retry.hidden = true;
    image.hidden = true;
    const controller = new AbortController();
    figure.requestController = controller;
    try {
        const response = await fetch('/api/image', {
            method: 'POST', headers: {'X-Reader-Key': key, 'Content-Type': 'application/json'},
            body: JSON.stringify({url: figure.dataset.source}), signal: controller.signal
        });
        if (!response.ok) {
            const error = await response.json();
            throw Error(error.error || '图片加载失败');
        }
        const blob = await response.blob();
        if (!figure.isConnected) return;
        if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
        const url = URL.createObjectURL(blob);
        image.dataset.objectUrl = url;
        image.src = url;
        await image.decode();
        if (!figure.isConnected) { URL.revokeObjectURL(url); return; }
        image.sourceBlob = blob;
        image.hidden = false;
        status.textContent = '点击图片放大';
    } catch (error) {
        if (!figure.isConnected || error.name === 'AbortError') return;
        status.textContent = error.name === 'EncodingError' ? '浏览器无法解码这张图片，文件可能已损坏。' : error.message;
        retry.hidden = false;
    } finally { figure.requestController = null; }
}

function openImage(image) {
    if (!image.sourceBlob) return;
    const viewer = node('dialog', 'image-viewer');
    const close = node('button', 'image-close', '关闭大图 ×');
    close.type = 'button';
    const full = node('img');
    full.alt = image.alt;
    const url = URL.createObjectURL(image.sourceBlob);
    full.src = url;
    viewer.append(close, full);
    document.body.append(viewer);
    close.addEventListener('click', () => viewer.close());
    viewer.addEventListener('close', () => { URL.revokeObjectURL(url); viewer.remove(); });
    viewer.showModal();
}

function imageFigure(raw, alt) {
    const figure = node('figure', 'post-image');
    const url = supportedImage(raw);
    if (!url) {
        figure.append(node('figcaption', '', '暂不支持此图片地址：' + raw));
        return figure;
    }
    figure.dataset.source = url;
    const image = node('img');
    image.alt = alt || '帖子图片';
    image.hidden = true;
    image.tabIndex = 0;
    image.addEventListener('click', () => openImage(image));
    image.addEventListener('keydown', event => { if (event.key === 'Enter') openImage(image); });
    const retry = node('button', 'image-retry outline', '重试图片');
    retry.type = 'button';
    retry.hidden = true;
    retry.addEventListener('click', () => queueImage(figure));
    figure.append(image, node('figcaption', '', '等待加载图片…'), retry);
    imageObserver.observe(figure);
    return figure;
}

function clearContent(root) {
    for (const figure of root.querySelectorAll('.post-image')) {
        imageObserver.unobserve(figure);
        figure.requestController?.abort();
        const image = figure.querySelector('img');
        if (image?.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
    }
    root.replaceChildren();
}

function renderContent(raw, preview = false) {
    const container = node('div', 'content');
    // Only recognize image syntax; all other post content stays text, never HTML.
    const tokens = /!\[([^\]\n]*)\]\(\s*(<[^>\n]+>|https?:\/\/[^\s)]+)(?:\s+["'][^"\n]*["'])?\s*\)|<img\b[^>]*>/gi;
    let last = 0, remaining = preview ? 500 : Infinity, count = 0, clipped = false;
    const addText = text => {
        if (text.length > remaining) clipped = true;
        const shown = text.slice(0, remaining);
        container.append(document.createTextNode(shown));
        remaining -= shown.length;
    };
    for (const match of String(raw).matchAll(tokens)) {
        addText(raw.slice(last, match.index));
        let url = match[2], alt = match[1] || '';
        if (!url) {
            const source = match[0].match(/\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i);
            url = source && (source[1] || source[2] || source[3]);
            const alternative = match[0].match(/\balt\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
            alt = alternative && (alternative[1] || alternative[2]) || '';
        }
        if (url && (!preview || count < 3)) {
            container.append(imageFigure(url.replace(/^<|>$/g, ''), decodeImageAttribute(alt)));
            count++;
        } else if (url) clipped = true;
        else addText(match[0]);
        last = match.index + match[0].length;
    }
    addText(raw.slice(last));
    if (clipped) container.append(node('span', 'content-more', '\n…更多内容请打开讨论详情'));
    return container;
}
