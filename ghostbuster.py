
import sys
import os
import argparse
import ctypes
import re
import psutil

VERSION = "1.0.0"

DEFAULT_TARGETS = ["chrome", "msedge", "node", "java", "discord", "code"]
DEFAULT_THRESHOLD_MB = 20.0

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

class Colors:
    CYAN = '\033[36m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    RED = '\033[31m'
    MAGENTA = '\033[35m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'

    @classmethod
    def disable(cls):
        cls.CYAN = ""
        cls.GREEN = ""
        cls.YELLOW = ""
        cls.RED = ""
        cls.MAGENTA = ""
        cls.BOLD = ""
        cls.UNDERLINE = ""
        cls.RESET = ""

def setup_terminal():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

    if not sys.stdout.isatty():
        Colors.disable()
        return

    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            stdout_handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(stdout_handle, mode.value | 0x0004)
        except Exception:
            Colors.disable()

def clean_len(string_val):
    return len(ANSI_ESCAPE.sub('', str(string_val)))

def is_admin():
    try:
        if sys.platform == "win32":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.getuid() == 0
    except Exception:
        return False

def get_pids_with_windows():
    pids = set()
    if sys.platform == "win32":
        EnumWindows = ctypes.windll.user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW

        def callback(hwnd, extra):
            if IsWindowVisible(hwnd) and GetWindowTextLengthW(hwnd) > 0:
                pid = ctypes.c_ulong()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(pid.value)
            return True

        callback_ref = EnumWindowsProc(callback)
        try:
            EnumWindows(callback_ref, 0)
        except Exception:
            pass

    return pids

def scan_ghost_processes(target_list, threshold_mb):
    ghosts = []
    pids_with_windows = get_pids_with_windows() if sys.platform == "win32" else set()

    for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'ppid']):
        try:
            p_name = proc.info['name']
            if not p_name:
                continue

            p_name_lower = p_name.lower()
            base_name = p_name_lower[:-4] if p_name_lower.endswith('.exe') else p_name_lower

            if not any(target in base_name for target in target_list):
                continue

            mem_info = proc.info['memory_info']
            if not mem_info:
                continue

            mem_usage = mem_info.rss / (1024 * 1024)
            if mem_usage < threshold_mb:
                continue

            is_ghost = True
            status_desc = ""

            if sys.platform == "win32":
                if proc.pid in pids_with_windows:
                    is_ghost = False
                else:
                    curr = proc
                    while True:
                        try:
                            parent = curr.parent()
                            if parent is None or parent.pid == curr.pid:
                                break
                            if parent.pid in pids_with_windows:
                                is_ghost = False
                                break
                            curr = parent
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            break
                status_desc = "Background / Windowless"
            else:
                terminal = None
                try:
                    terminal = proc.terminal()
                except Exception:
                    pass

                try:
                    parent = proc.parent()
                    parent_pid = parent.pid if parent else 1
                except Exception:
                    parent_pid = 1

                if terminal is not None:
                    is_ghost = False
                elif parent_pid == 1:
                    status_desc = "Orphaned (No Parent)"
                else:
                    status_desc = "Background Service"

            if is_ghost:
                ghosts.append({
                    'pid': proc.pid,
                    'name': p_name,
                    'memory': mem_usage,
                    'parent_pid': proc.ppid(),
                    'status': status_desc
                })

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return ghosts

def display_ghosts(ghost_list):
    headers = ["PID", "Process Name", "Memory Usage", "Parent PID", "Status Details"]
    rows = []

    for g in ghost_list:
        pid_str = f"{Colors.CYAN}{g['pid']}{Colors.RESET}"
        name_str = f"{Colors.BOLD}{g['name']}{Colors.RESET}"
        mem_str = f"{Colors.GREEN}{g['memory']:.2f} MB{Colors.RESET}"

        parent_name = "Unknown"
        if g['parent_pid']:
            try:
                parent = psutil.Process(g['parent_pid'])
                parent_name = parent.name()
            except Exception:
                pass

        parent_str = f"{parent_name} ({g['parent_pid']})" if g['parent_pid'] else "None"
        status_str = f"{Colors.YELLOW}{g['status']}{Colors.RESET}"

        rows.append([pid_str, name_str, mem_str, parent_str, status_str])

    widths = [clean_len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], clean_len(val))

    top = f"{Colors.CYAN}┌" + "┬".join("─" * (w + 2) for w in widths) + f"┐{Colors.RESET}"
    header_str = (
        f"{Colors.CYAN}│{Colors.RESET}" +
        f"{Colors.CYAN}│{Colors.RESET}".join(f" {Colors.BOLD}{headers[i]:<{widths[i]}}{Colors.RESET} " for i in range(len(headers))) +
        f"{Colors.CYAN}│{Colors.RESET}"
    )
    sep = f"{Colors.CYAN}├" + "┼".join("─" * (w + 2) for w in widths) + f"┤{Colors.RESET}"
    bottom = f"{Colors.CYAN}└" + "┴".join("─" * (w + 2) for w in widths) + f"┘{Colors.RESET}"

    print(top)
    print(header_str)
    print(sep)
    for row in rows:
        formatted_cells = []
        for i, cell in enumerate(row):
            padding = widths[i] - clean_len(cell)
            formatted_cells.append(f" {cell}{' ' * padding} ")
        row_line = f"{Colors.CYAN}│{Colors.RESET}" + f"{Colors.CYAN}│{Colors.RESET}".join(formatted_cells) + f"{Colors.CYAN}│{Colors.RESET}"
        print(row_line)
    print(bottom)

def terminate_ghosts(ghost_list):
    saved_ram = 0.0
    killed_count = 0

    print(f"\n{Colors.YELLOW}⚡ Initiating cleanup process...{Colors.RESET}\n")

    for proc_info in ghost_list:
        pid = proc_info['pid']
        name = proc_info['name']
        mem = proc_info['memory']

        try:
            p = psutil.Process(pid)
            p.terminate()

            try:
                p.wait(timeout=1.0)
            except psutil.TimeoutExpired:
                p.kill()

            saved_ram += mem
            killed_count += 1
            print(f"  {Colors.GREEN}✓{Colors.RESET} Terminated {Colors.BOLD}{name}{Colors.RESET} (PID: {pid}) -> Freed {mem:.2f} MB")

        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            print(f"  {Colors.RED}✗{Colors.RESET} Failed to terminate {name} (PID: {pid}): Access Denied / Exited")
        except Exception as e:
            print(f"  {Colors.RED}✗{Colors.RESET} Error terminating {name} (PID: {pid}): {e}")

    print(f"\n{Colors.GREEN}{Colors.BOLD}⚡ Execution Complete!{Colors.RESET}")
    print(f"Cleaned {Colors.BOLD}{killed_count}{Colors.RESET} ghost process(es) and instantly freed {Colors.GREEN}{Colors.BOLD}{saved_ram:.2f} MB{Colors.RESET} of RAM.\n")

def print_logo():
    logo = (
        f"{Colors.CYAN}{Colors.BOLD}\n"
        "  ________ __                    __  ________               __                  \n"
        " /  _____//  |__   ____   ______/  |_\\______ \\  __ __  ____/  |_  ___________\n"
        "/   \\  ___|  |  \\ /  _ \\ /  ___/\\   __\\    |  _\\|  |  \\/ ___/\\   __\\/ __ \\_  __ \\\n"
        "\\    \\_\\  \\   Y  (  <_> )___ \\   |  | |    |   \\  |  / /_/  >|  | \\  ___/|  | \\/\n"
        " \\______  /___|  /\\____/____  >  |__|/_______  /____/\\___  / |__|  \\___  >__|   \n"
        "        \\/     \\/           \\/               \\/     /_____/            \\/       \n"
        f"                    [ PC Zombie Process Sweeper v{VERSION} ]{Colors.RESET}\n"
    )
    print(logo)

def main():
    setup_terminal()

    parser = argparse.ArgumentParser(
        description="GhostBuster-PC: Detect and clean up frozen or orphaned background processes."
    )
    parser.add_argument(
        "-t", "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_MB,
        help=f"Minimum RAM usage in MB to flag a process (default: {DEFAULT_THRESHOLD_MB})"
    )
    parser.add_argument(
        "-g", "--targets",
        type=str,
        help="Comma-separated list of target process names to scan (case-insensitive)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-confirm and terminate all detected ghost processes without prompting"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and display findings, but do not terminate any processes"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"GhostBuster-PC v{VERSION}"
    )

    args = parser.parse_args()

    print_logo()

    target_list = DEFAULT_TARGETS
    if args.targets:
        target_list = [t.strip().lower() for t in args.targets.split(",")]

    if not is_admin():
        print(f"{Colors.YELLOW}⚠️  Note: Running as a standard user. For deep cleaning of restricted system-level tasks, "
              f"run this tool in an Elevated/Administrator terminal.{Colors.RESET}\n")

    print(f"🔍 Scanning system for targets: {Colors.CYAN}{', '.join(target_list)}{Colors.RESET} (Memory Threshold: > {args.threshold} MB)...")

    ghosts = scan_ghost_processes(target_list, args.threshold)

    if not ghosts:
        print(f"\n{Colors.GREEN}🎉 Your PC is clean! No stuck or windowless background instances of targeted apps detected.{Colors.RESET}\n")
        return

    print(f"\n{Colors.YELLOW}⚠️  Found {len(ghosts)} background instances hogging resources:{Colors.RESET}\n")

    display_ghosts(ghosts)

    total_mem = sum(g['memory'] for g in ghosts)
    print(f"\n📊 Total reclaimable RAM: {Colors.GREEN}{Colors.BOLD}{total_mem:.2f} MB{Colors.RESET}\n")

    if args.dry_run:
        print(f"{Colors.CYAN}Dry run enabled. No processes were modified.{Colors.RESET}\n")
        return

    if args.yes:
        confirm = "y"
    else:
        try:
            confirm = input(f"Would you like to force-close these background tasks? (y/n) [{Colors.BOLD}y{Colors.RESET}]: ").strip().lower()
            if not confirm:
                confirm = "y"
        except (KeyboardInterrupt, EOFError):
            print(f"\n{Colors.RED}❌ Operation canceled.{Colors.RESET}\n")
            return

    if confirm in ('y', 'yes'):
        terminate_ghosts(ghosts)
    else:
        print(f"{Colors.RED}❌ Operation canceled.{Colors.RESET}\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.RED}❌ Program terminated by user.{Colors.RESET}\n")
        sys.exit(0)
