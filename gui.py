
import sys
import os
import socket
import threading
import http.server
import json
import urllib.parse
import webbrowser
import time
from http import HTTPStatus
import psutil

_original_print = print
def print(*args, **kwargs):
    if sys.stdout is not None:
        try:
            _original_print(*args, **kwargs)
        except Exception:
            pass

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

class GhostBusterRequestHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)

        if parsed_path.path == '/':
            try:
                base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
                html_path = os.path.join(base_dir, 'index.html')
                with open(html_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(content.encode('utf-8'))
            except Exception as e:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"HTML render failed: {e}")

        elif parsed_path.path == '/api/scan':
            try:
                query_params = urllib.parse.parse_qs(parsed_path.query)
                threshold = float(query_params.get('threshold', [20.0])[0])

                targets_param = query_params.get('targets', [None])[0]
                from ghostbuster import scan_ghost_processes, DEFAULT_TARGETS

                if targets_param:
                    target_list = [t.strip().lower() for t in targets_param.split(',') if t.strip()]
                else:
                    target_list = DEFAULT_TARGETS

                ghosts = scan_ghost_processes(target_list, threshold)

                enriched = []
                for g in ghosts:
                    parent_name = "Unknown"
                    if g['parent_pid']:
                        try:
                            parent = psutil.Process(g['parent_pid'])
                            parent_name = parent.name()
                        except Exception:
                            pass

                    enriched.append({
                        'pid': g['pid'],
                        'name': g['name'],
                        'memory': g['memory'],
                        'parent_pid': g['parent_pid'],
                        'parent_name': parent_name,
                        'status': g['status']
                    })

                vmem = psutil.virtual_memory()
                system_ram = {
                    'total_gb': vmem.total / (1024 * 1024 * 1024),
                    'used_gb': vmem.used / (1024 * 1024 * 1024),
                    'percent': vmem.percent
                }

                self.send_response(HTTPStatus.OK)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'ghosts': enriched,
                    'system_ram': system_ram
                }).encode('utf-8'))
            except Exception as e:

                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)

        if parsed_path.path == '/api/terminate':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                req_body = json.loads(post_data.decode('utf-8'))

                target_pid = req_body.get('pid')
                terminate_all = req_body.get('all', False)

                freed_ram = 0.0
                killed_count = 0
                error_msg = None

                if terminate_all:
                    from ghostbuster import scan_ghost_processes, DEFAULT_TARGETS
                    ghosts = scan_ghost_processes(DEFAULT_TARGETS, 10.0)
                    for g in ghosts:
                        try:
                            p = psutil.Process(g['pid'])
                            p.terminate()
                            try:
                                p.wait(timeout=0.5)
                            except psutil.TimeoutExpired:
                                p.kill()
                            freed_ram += g['memory']
                            killed_count += 1
                        except Exception:
                            continue
                elif target_pid:
                    try:
                        p = psutil.Process(target_pid)
                        mem = p.memory_info().rss / (1024 * 1024)
                        p.terminate()
                        try:
                            p.wait(timeout=0.5)
                        except psutil.TimeoutExpired:
                            p.kill()
                        freed_ram = mem
                        killed_count = 1
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        error_msg = f"Failed to terminate process: Access Denied"
                    except Exception as e:
                        error_msg = str(e)
                else:
                    error_msg = "Invalid params"

                self.send_response(HTTPStatus.OK if not error_msg else HTTPStatus.BAD_REQUEST)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()

                response_data = {
                    'success': error_msg is None,
                    'freed_memory': freed_ram,
                    'killed_count': killed_count
                }
                if error_msg:
                    response_data['error'] = error_msg
                self.wfile.write(json.dumps(response_data).encode('utf-8'))

            except Exception as e:
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

def find_free_port():
    for port in range(5000, 5050):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except socket.error:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def start_server(port):
    server = http.server.HTTPServer(('127.0.0.1', port), GhostBusterRequestHandler)
    server.serve_forever()

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

    port = find_free_port()

    server_thread = threading.Thread(target=start_server, args=(port,))
    server_thread.daemon = True
    server_thread.start()

    url = f"http://127.0.0.1:{port}"
    print(f"⚡ GhostBuster-PC Server started on: {url}")

    webview_installed = False
    try:
        import webview
        webview_installed = True
    except ImportError:
        print("\nℹ️  To run this as a standalone desktop window, install 'pywebview':")
        print("   pip install pywebview\n")

    if webview_installed:
        try:
            print("🚀 Launching native window GUI wrapper...")
            webview.create_window(
                title="GhostBuster-PC | Dashboard",
                url=url,
                width=920,
                height=700,
                resizable=True,
                background_color='#060913'
            )
            webview.start()
            print("👋 Window closed. Server stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"⚠️  Native GUI wrapper initialization failed: {e}")
            print("Falling back to default web browser display...")

    print("\n" + "="*60)
    print(f"🔗 Opened Dashboard in default browser: {url}")
    print("Press Ctrl+C in this terminal to terminate the server daemon.")
    print("="*60 + "\n")
    webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⚡ Server terminated. Goodbye!")

if __name__ == '__main__':
    main()
