"""Streamlit Web Dashboard for HushFile."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from butler.config import load_config
from butler.digest import get_recent_digest
from butler.journal import Journal
from butler.mover import safe_filesystem_move
from butler.search import search_documents

st.set_page_config(
    page_title="HushFile",
    page_icon="🤫",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for polished interface
st.markdown(
    """
    <style>
    .main-header { font-size: 2.2rem; font-weight: 700; color: #1E88E5; margin-bottom: 0.2rem; }
    .sub-header { color: #607D8B; font-size: 1.1rem; margin-bottom: 1.5rem; }
    .metric-card { background: #F8F9FA; border-radius: 8px; padding: 1rem; border-left: 4px solid #1E88E5; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_backend():
    cfg = load_config()
    journal = Journal(cfg.db_path)
    return cfg, journal


cfg, journal = get_backend()

st.sidebar.title("🤫 HushFile Control")
st.sidebar.markdown(f"**Watching:** `{cfg.watch_dir.name}`")
st.sidebar.markdown(f"**Organized:** `{cfg.output_dir.name}`")
st.sidebar.markdown(f"**Text Model:** `{cfg.models.text}`")
st.sidebar.markdown("---")

tab_choice = st.sidebar.radio(
    "Navigation",
    ["📊 Recent Activity", "🔍 Semantic Search", "📥 Review Queue", "⏮ History & Undo"],
)

# TAB 1: Recent Activity
if tab_choice == "📊 Recent Activity":
    st.markdown('<div class="main-header">Activity Digest</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Drop it in Downloads, forget it, find it later.</div>', unsafe_allow_html=True)

    digest = get_recent_digest(journal)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Categories Active", len(digest["category_counts"]))
    with c2:
        st.metric("Duplicates Filtered", digest["duplicate_count"])
    with c3:
        st.metric("Pending Review", digest["review_count"])
    with c4:
        st.metric("Failures", digest["failure_count"])

    st.markdown("### Files by Category")
    if digest["category_counts"]:
        st.bar_chart(digest["category_counts"])
    else:
        st.info("No files processed yet today.")

    st.markdown("### Recent Handled Files")
    if digest["recent_files"]:
        for f in digest["recent_files"]:
            with st.expander(f"📄 {f['original_name']} ➔ {Path(f['dst_path']).name if f.get('dst_path') else 'N/A'}"):
                card = journal.get_card(f["file_id"])
                if card:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.write(f"**Category:** {card.get('category')}")
                        st.write(f"**Document Type:** {card.get('document_type')}")
                        st.write(f"**Confidence:** {card.get('confidence'):.2f}" if card.get('confidence') else "**Confidence:** N/A")
                        st.write(f"**Current Location:** `{card.get('current_path')}`")
                    with col_b:
                        st.write(f"**Summary:** {card.get('summary')}")
                        st.write(f"**Tags:** {', '.join(card.get('tags', []))}")
                        st.write(f"**Reason:** {card.get('reason')}")
    else:
        st.write("No recent files.")

# TAB 2: Semantic Search
elif tab_choice == "🔍 Semantic Search":
    st.markdown('<div class="main-header">Semantic Search</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Find your files by meaning, not filename.</div>', unsafe_allow_html=True)

    query = st.text_input("Enter search query (e.g. 'electric utility bill from last month', 'biology lecture slides')", "")
    col_filter1, col_filter2 = st.columns(2)
    with col_filter1:
        cat_filter = st.selectbox("Filter Category (optional)", ["All"] + cfg.categories)
    with col_filter2:
        limit_count = st.slider("Max Results", 1, 15, 5)

    if query.strip():
        with st.spinner("Searching local ChromaDB vector store..."):
            hits = search_documents(
                query=query,
                config=cfg,
                limit=limit_count,
                doc_type=None if cat_filter == "All" else cat_filter,
            )

        if hits:
            st.markdown(f"**Found {len(hits)} matching documents:**")
            for h in hits:
                with st.container():
                    st.markdown(f"#### 📄 {Path(h['current_path']).name}")
                    st.caption(f"Category: **{h['category']}** | Similarity: **{h['similarity']*100:.1f}%**")
                    st.write(h["summary"])
                    st.code(h["current_path"])
                    st.markdown("---")
        else:
            st.warning("No matching documents found.")

# TAB 3: Review Queue
elif tab_choice == "📥 Review Queue":
    st.markdown('<div class="main-header">Review Queue</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Files with confidence below threshold waiting for confirmation.</div>', unsafe_allow_html=True)

    review_files = journal.get_review_files()
    if not review_files:
        st.success("🎉 The Review Queue is empty! All files filed with high confidence.")
    else:
        for f in review_files:
            file_id = f["id"]
            with st.container():
                st.markdown(f"### ⚠️ {f['original_name']}")
                st.write(f"**Reason:** {f.get('reason', 'Low confidence')}")
                st.write(f"**Current Path:** `{f['current_path']}`")

                new_cat = st.selectbox(
                    f"Choose Destination Category for #{file_id}:",
                    cfg.categories,
                    key=f"cat_select_{file_id}",
                )

                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button(f"Move to '{new_cat}'", key=f"btn_move_{file_id}"):
                        cur_p = Path(f["current_path"])
                        dst_folder = cfg.output_dir / new_cat
                        dst_folder.mkdir(parents=True, exist_ok=True)
                        new_dst = dst_folder / cur_p.name

                        batch_id = journal.start_batch(trigger="ui_review")
                        mid = journal.record_move_pending(
                            batch_id=batch_id,
                            file_id=file_id,
                            src_path=str(cur_p),
                            dst_path=str(new_dst),
                            sha256=f["sha256"],
                            action="move",
                            reason=f"Manually reviewed in dashboard to {new_cat}",
                            confidence=1.0,
                            model="ui:user_review",
                        )
                        safe_filesystem_move(cur_p, new_dst)
                        journal.mark_move_done(mid, str(new_dst))
                        journal.save_card(file_id=file_id, category=new_cat, confidence=1.0)
                        journal.finish_batch(batch_id)
                        st.success(f"Moved to {new_cat}!")
                        st.rerun()

                st.markdown("---")

# TAB 4: History & Undo
elif tab_choice == "⏮ History & Undo":
    st.markdown('<div class="main-header">History & Safe Undo</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Every move is logged in the crash-safe SQLite journal and fully reversible.</div>', unsafe_allow_html=True)

    col_u1, col_u2 = st.columns([3, 1])
    with col_u2:
        if st.button("⏮ Undo Most Recent Batch", type="primary"):
            try:
                res = journal.undo()
                st.success(f"Reversed batch! {len(res)} file(s) restored.")
                st.rerun()
            except Exception as e:
                st.error(f"Cannot undo: {e}")

    moves = journal.get_history(limit=50)
    if moves:
        # Group moves by batch
        batches_map: dict[int, list[dict]] = {}
        for m in moves:
            b_id = m["batch_id"]
            batches_map.setdefault(b_id, []).append(m)

        for b_id, b_moves in batches_map.items():
            first_m = b_moves[0]
            dry_str = " (DRY RUN)" if first_m.get("dry_run") else ""
            with st.expander(f"📦 Batch #{b_id}{dry_str} — {first_m['timestamp'][:19].replace('T', ' ')} ({len(b_moves)} moves)"):
                if not first_m.get("dry_run"):
                    if st.button(f"Undo Entire Batch #{b_id}", key=f"undo_b_{b_id}"):
                        try:
                            journal.undo(batch_id=b_id)
                            st.success(f"Batch #{b_id} undone successfully!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to undo: {e}")

                for mv in b_moves:
                    st.write(
                        f"• **Move #{mv['id']}** ({mv['action']}): `{mv['original_name']}` ➔ `{Path(mv['dst_path']).name if mv.get('dst_path') else 'N/A'}` [{mv['status']}]"
                    )
    else:
        st.write("No operations recorded yet.")
