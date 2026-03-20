"""Documentation browser page."""

import os
import logging

from nicegui import ui, run

log = logging.getLogger('axc.docs')

AXC_HOME = os.environ.get('AXC_HOME', os.path.expanduser('~/projects/axc-trading'))
DOCS_ROOT = os.path.join(AXC_HOME, 'docs')


def _list_docs() -> list[dict]:
    """List all .md files in docs/ directory."""
    docs = []
    if not os.path.isdir(DOCS_ROOT):
        return docs
    for root, _, files in os.walk(DOCS_ROOT):
        for f in sorted(files):
            if f.endswith('.md'):
                rel = os.path.relpath(os.path.join(root, f), DOCS_ROOT)
                docs.append({'name': f, 'path': rel, 'full_path': os.path.join(root, f)})
    return docs


def _read_doc(path: str) -> str:
    """Read a doc file safely."""
    full = os.path.join(DOCS_ROOT, path)
    # Path traversal guard
    if not os.path.realpath(full).startswith(os.path.realpath(DOCS_ROOT)):
        return '**Access denied**'
    try:
        with open(full, 'r') as f:
            return f.read()
    except Exception as e:
        return f'**Error reading file:** {e}'


def render_docs_page():
    """Render documentation browser."""
    docs = _list_docs()

    if not docs:
        ui.label('No documentation files found').classes('text-gray-500')
        return

    with ui.splitter(value=25).classes('w-full h-[calc(100vh-120px)]') as splitter:
        with splitter.before:
            # Sidebar TOC
            ui.label('FILES').classes('text-[10px] text-gray-500 uppercase tracking-wide p-2')

            search = ui.input(placeholder='Search...').classes('w-full px-2').props('dense outlined dark')

            doc_list = ui.column().classes('gap-0 overflow-y-auto')

            content_area = None  # Will be set below

            def load_doc(path: str):
                nonlocal content_area
                if content_area is None:
                    return
                text = _read_doc(path)
                content_area.clear()
                with content_area:
                    ui.markdown(text).classes('prose prose-invert max-w-none')

            def filter_docs():
                q = (search.value or '').lower()
                doc_list.clear()
                with doc_list:
                    for doc in docs:
                        if q and q not in doc['name'].lower() and q not in doc['path'].lower():
                            continue
                        ui.button(doc['path'], on_click=lambda p=doc['path']: load_doc(p)) \
                            .classes('w-full justify-start text-left') \
                            .props('flat dense no-caps color=grey-5 size=sm')

            search.on('update:model-value', filter_docs)
            filter_docs()

        with splitter.after:
            content_area = ui.scroll_area().classes('p-4')
            with content_area:
                ui.label('Select a document from the sidebar').classes('text-gray-500')
