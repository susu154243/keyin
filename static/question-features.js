/**
 * 题目互动功能：纠错 / 笔记 / 留言板
 */
document.addEventListener('DOMContentLoaded', function() {
    const MAX_CHARS = 500;
    const MAX_SIZE = 5 * 1024 * 1024; // 5MB

    // ==================== 通用工具函数 ====================

    function showModal(modalId) {
        document.getElementById(modalId).style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }

    function hideModal(modalId) {
        document.getElementById(modalId).style.display = 'none';
        document.body.style.overflow = '';
    }

    function updateCharCount(textareaId, countId) {
        const ta = document.getElementById(textareaId);
        const count = document.getElementById(countId);
        count.textContent = ta.value.length;
        if (ta.value.length > MAX_CHARS) {
            ta.value = ta.value.substring(0, MAX_CHARS);
            count.textContent = MAX_CHARS;
        }
        count.style.color = ta.value.length > 450 ? '#e63946' : '#999';
    }

    async function uploadImage(fileInput, previewId) {
        const file = fileInput.files[0];
        if (!file) return null;
        if (file.size > MAX_SIZE) {
            alert('图片大小不能超过5MB');
            fileInput.value = '';
            return null;
        }

        const formData = new FormData();
        formData.append('image', file);

        try {
            const resp = await fetch('/api/upload-image', {
                method: 'POST',
                body: formData
            });
            const data = await resp.json();
            if (data.success) {
                const preview = document.getElementById(previewId);
                preview.innerHTML = `<img src="${data.url}" style="max-width:100%;max-height:200px;border-radius:8px;margin-top:8px;">`;
                return data.url;
            } else {
                alert(data.error || '上传失败');
                return null;
            }
        } catch (e) {
            alert('上传失败：' + e.message);
            return null;
        }
    }

    // ==================== 纠错反馈 ====================

    const feedbackBtn = document.getElementById('feedbackBtn');
    if (feedbackBtn) {
        feedbackBtn.addEventListener('click', () => showModal('feedbackModal'));
    }

    const feedbackSubmit = document.getElementById('feedbackSubmit');
    if (feedbackSubmit) {
        feedbackSubmit.addEventListener('click', async function() {
            const content = document.getElementById('feedbackContent').value.trim();
            if (!content) { alert('请填写反馈内容'); return; }
            if (content.length > MAX_CHARS) { alert('内容不超过500字'); return; }

            this.disabled = true;
            this.textContent = '提交中...';

            try {
                const resp = await fetch(feedbackSubmit.dataset.url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content })
                });
                const data = await resp.json();
                if (data.success) {
                    alert('反馈已提交，感谢你的纠错建议！');
                    hideModal('feedbackModal');
                    document.getElementById('feedbackContent').value = '';
                    document.getElementById('feedbackPreview').innerHTML = '';
                } else {
                    alert(data.error || '提交失败');
                }
            } catch (e) {
                alert('提交失败：' + e.message);
            }
            this.disabled = false;
            this.textContent = '提交反馈';
        });
    }

    // 纠错图片上传
    const feedbackImage = document.getElementById('feedbackImage');
    if (feedbackImage) {
        feedbackImage.addEventListener('change', () => uploadImage(feedbackImage, 'feedbackPreview'));
    }

    // ==================== 笔记 ====================

    const noteBtn = document.getElementById('noteBtn');
    if (noteBtn) {
        noteBtn.addEventListener('click', async () => {
            showModal('noteModal');
            // 加载已有笔记
            try {
                const resp = await fetch(noteBtn.dataset.url);
                const data = await resp.json();
                if (data.note) {
                    document.getElementById('noteContent').value = data.note.content;
                    if (data.note.image_path) {
                        document.getElementById('notePreview').innerHTML =
                            `<img src="${data.note.image_path}" style="max-width:100%;max-height:200px;border-radius:8px;margin-top:8px;">`;
                    }
                } else {
                    document.getElementById('noteContent').value = '';
                    document.getElementById('notePreview').innerHTML = '';
                }
                updateCharCount('noteContent', 'noteCount');
            } catch (e) { console.error('加载笔记失败', e); }
        });
    }

    const noteSave = document.getElementById('noteSave');
    if (noteSave) {
        noteSave.addEventListener('click', async function() {
            const content = document.getElementById('noteContent').value.trim();
            if (content.length > MAX_CHARS) { alert('笔记不超过500字'); return; }

            this.disabled = true;
            this.textContent = '保存中...';

            try {
                const resp = await fetch(noteSave.dataset.url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content })
                });
                const data = await resp.json();
                if (data.success) {
                    alert('笔记已保存');
                    hideModal('noteModal');
                } else {
                    alert(data.error || '保存失败');
                }
            } catch (e) {
                alert('保存失败：' + e.message);
            }
            this.disabled = false;
            this.textContent = '保存笔记';
        });
    }

    // 笔记图片上传
    const noteImage = document.getElementById('noteImage');
    if (noteImage) {
        noteImage.addEventListener('change', () => uploadImage(noteImage, 'notePreview'));
    }

    // ==================== 留言板 ====================

    const commentBtn = document.getElementById('commentBtn');
    if (commentBtn) {
        commentBtn.addEventListener('click', () => showModal('commentModal'));
    }

    // 页面加载时自动加载留言墙
    if (commentBtn) loadCommentWall(1);

    async function loadCommentWall(page) {
        const list = document.getElementById('commentWallList');
        if (!list) return;
        list.innerHTML = '<p class="comment-wall-loading">加载中...</p>';

        try {
            const resp = await fetch(commentBtn.dataset.url + '?page=' + page);
            const data = await resp.json();
            if (!data.success || !data.comments) {
                list.innerHTML = '<p class="cw-empty">加载失败</p>'; return;
            }

            if (data.comments.length === 0) {
                list.innerHTML = '<p class="cw-empty">暂无留言，来做第一个留言的人吧！</p>';
                return;
            }

            list.innerHTML = data.comments.map(c => `
                <div class="cw-item">
                    <div class="cw-header">
                        <span class="cw-user">${escapeHtml(c.username)}</span>
                        <span class="cw-time">${c.created_at || ''}</span>
                    </div>
                    <div class="cw-content">${escapeHtml(c.content)}</div>
                    ${c.image_path ? `<img src="${c.image_path}" alt="留言图片">` : ''}
                    ${c.current_user ? `<button class="cw-delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            `).join('');

            // 分页
            const pag = document.getElementById('commentWallPagination');
            if (pag && data.total > 20) {
                const totalPages = Math.ceil(data.total / 20);
                let pages = '';
                for (let i = 1; i <= totalPages; i++) {
                    pages += `<button class="page-btn ${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`;
                }
                pag.innerHTML = pages;
                pag.querySelectorAll('.page-btn').forEach(b => {
                    b.addEventListener('click', () => loadCommentWall(parseInt(b.dataset.page)));
                });
            }

            // 删除
            list.querySelectorAll('.cw-delete').forEach(b => {
                b.addEventListener('click', async function() {
                    if (!confirm('确定删除此留言？')) return;
                    const cid = this.dataset.id;
                    const deleteUrl = commentBtn.dataset.url.replace('/comments', '/delete-comment');
                    const resp2 = await fetch(deleteUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ comment_id: parseInt(cid) })
                    });
                    const d2 = await resp2.json();
                    if (d2.success) loadCommentWall(1);
                    else alert(d2.error || '删除失败');
                });
            });

        } catch (e) {
            list.innerHTML = '<p class="cw-empty">加载失败</p>';
        }
    }

    async function loadComments(page) {
        const list = document.getElementById('commentList');
        list.innerHTML = '<p style="text-align:center;color:#999;">加载中...</p>';

        try {
            const resp = await fetch(commentBtn.dataset.url + '?page=' + page);
            const data = await resp.json();
            if (!data.success) { list.innerHTML = '<p>加载失败</p>'; return; }

            if (data.comments.length === 0) {
                list.innerHTML = '<p style="text-align:center;color:#999;">暂无留言，来做第一个留言的人吧！</p>';
                return;
            }

            list.innerHTML = data.comments.map(c => `
                <div class="comment-item">
                    <div class="comment-header">
                        <span class="comment-user">${escapeHtml(c.username)}</span>
                        <span class="comment-time">${c.created_at}</span>
                    </div>
                    <div class="comment-content">${escapeHtml(c.content)}</div>
                    ${c.image_path ? `<img src="${c.image_path}" style="max-width:100%;max-height:150px;border-radius:8px;margin-top:8px;">` : ''}
                    ${c.current_user ? `<button class="comment-delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            `).join('');

            // 分页
            const pagination = document.getElementById('commentPagination');
            if (data.total > 20) {
                const totalPages = Math.ceil(data.total / 20);
                let pages = '';
                for (let i = 1; i <= totalPages; i++) {
                    pages += `<button class="page-btn ${i === page ? 'active' : ''}" data-page="${i}">${i}</button>`;
                }
                pagination.innerHTML = pages;
                pagination.querySelectorAll('.page-btn').forEach(btn => {
                    btn.addEventListener('click', () => loadComments(parseInt(btn.dataset.page)));
                });
            } else {
                pagination.innerHTML = '';
            }

            // 删除按钮
            list.querySelectorAll('.comment-delete').forEach(btn => {
                btn.addEventListener('click', async function() {
                    if (!confirm('确定删除此留言？')) return;
                    const cid = this.dataset.id;
                    const resp2 = await fetch(commentBtn.dataset.url.replace('/comments', '/delete-comment'), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ comment_id: parseInt(cid) })
                    });
                    const d2 = await resp2.json();
                    if (d2.success) loadComments(1);
                    else alert(d2.error || '删除失败');
                });
            });

        } catch (e) {
            list.innerHTML = '<p style="text-align:center;color:#999;">加载失败</p>';
        }
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // 弹窗中发布留言
    const commentSubmit = document.getElementById('commentSubmit');
    if (commentSubmit) {
        commentSubmit.addEventListener('click', async function() {
            const content = document.getElementById('commentContent').value.trim();
            if (!content) { alert('请填写留言内容'); return; }
            if (content.length > MAX_CHARS) { alert('留言不超过500字'); return; }

            this.disabled = true;
            this.textContent = '发送中...';

            try {
                const resp = await fetch(commentSubmit.dataset.url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content })
                });
                const data = await resp.json();
                if (data.success) {
                    document.getElementById('commentContent').value = '';
                    document.getElementById('commentPreview').innerHTML = '';
                    updateCharCount('commentContent', 'commentCount');
                    hideModal('commentModal');
                    loadCommentWall(1); // 刷新留言墙
                } else {
                    alert(data.error || '发送失败');
                }
            } catch (e) {
                alert('发送失败：' + e.message);
            }
            this.disabled = false;
            this.textContent = '发布';
        });
    }

    // 留言图片上传
    const commentImage = document.getElementById('commentImage');
    if (commentImage) {
        commentImage.addEventListener('change', () => uploadImage(commentImage, 'commentPreview'));
    }

    // 关闭弹窗
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', function() {
            const modal = this.closest('.modal-overlay');
            if (modal) {
                modal.style.display = 'none';
                document.body.style.overflow = '';
            }
        });
    });

    // 点击遮罩关闭
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', function(e) {
            if (e.target === this) {
                this.style.display = 'none';
                document.body.style.overflow = '';
            }
        });
    });
});
