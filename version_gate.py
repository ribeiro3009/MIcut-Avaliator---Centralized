# version_gate.py  (Python 3.11 32-bits) - INDENTAÇÃO: 4 ESPAÇOS, SEM TABS
from __future__ import annotations
import sys
import os
import oracledb


# ===== CONFIGURAÇÕES =====
APP_NAME = "MICUT_AVALIATOR.EXE"
LOCAL_VERSION = "1.0.0.2"  # atualize a cada release, se não ler do .exe

DB_DSN = (

)
   # ex.: "10.0.0.1:1521/ORCLPDB1"
DB_USER = 

DB_PASS = 


def get_file_version_windows(exe_path: str) -> str | None:
    """
    Lê a FileVersion (ex.: 1.0.0.0) do executável no Windows via ctypes.
    Retorna None se não houver recurso de versão.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(exe_path, None)
        if size == 0:
            return None
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe_path, 0, size, data):
            return None

        pbuf = ctypes.c_void_p()
        ulen = wt.UINT()
        if not ver.VerQueryValueW(data, "\\", ctypes.byref(pbuf), ctypes.byref(ulen)):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wt.DWORD),
                ("dwStrucVersion", wt.DWORD),
                ("dwFileVersionMS", wt.DWORD),
                ("dwFileVersionLS", wt.DWORD),
                ("dwProductVersionMS", wt.DWORD),
                ("dwProductVersionLS", wt.DWORD),
                ("dwFileFlagsMask", wt.DWORD),
                ("dwFileFlags", wt.DWORD),
                ("dwFileOS", wt.DWORD),
                ("dwFileType", wt.DWORD),
                ("dwFileSubtype", wt.DWORD),
                ("dwFileDateMS", wt.DWORD),
                ("dwFileDateLS", wt.DWORD),
            ]

        info = VS_FIXEDFILEINFO.from_address(pbuf.value)
        if info.dwSignature != 0xFEEF04BD:
            return None

        major = info.dwFileVersionMS >> 16
        minor = info.dwFileVersionMS & 0xFFFF
        build = info.dwFileVersionLS >> 16
        rev = info.dwFileVersionLS & 0xFFFF
        return f"{major}.{minor}.{build}.{rev}"
    except Exception:
        return None


def version_to_tuple(v: str) -> tuple[int, int, int, int]:
    """Converte 'a.b.c.d' em tupla de 4 inteiros (faltantes viram 0)."""
    parts = (v or "").split(".")
    nums: list[int] = []
    for i in range(4):
        try:
            nums.append(int(parts[i]))
        except Exception:
            nums.append(0)
    return tuple(nums[:4])  # type: ignore[return-value]


def compare_versions(a: str, b: str) -> int:
    """
    Compara 'a' e 'b' numericamente.
    Retorna: 1 se a>b, 0 se a==b, -1 se a<b
    """
    ta, tb = version_to_tuple(a), version_to_tuple(b)
    if ta > tb:
        return 1
    elif ta < tb:
        return -1
    else:
        return 0


def init_oracle_client_if_available() -> None:
    """
    Tenta inicializar modo thick (Instant Client). Se não achar, segue thin.
    Bitness do Instant Client deve casar com o Python (32-bits).
    """
    try:
        ic_dir = os.environ.get("ORACLE_IC_DIR", r"C:\Oracle\instantclient_19_17")
        if os.path.isdir(ic_dir):
            oracledb.init_oracle_client(lib_dir=ic_dir)
    except Exception:
        # Sem problemas — continua em modo thin
        pass


def check_version_oracle(app_name: str, local_version: str) -> tuple[bool, str]:
    """
    Busca em DETRAN.VERSOES a linha do app e aplica a regra:
      TE_TAG = '0' -> exige versão exata
      TE_TAG = '1' -> permite versão mínima (>=)
    Retorna (permitido, mensagem)
    """
    init_oracle_client_if_available()

    conn = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT nu_versao, te_tag
                  FROM detran.versoes
                 WHERE UPPER(no_nome) = UPPER(:nome)
                """,
                {"nome": app_name},
            )
            row = cur.fetchone()
            if not row:
                return (False, "Aplicativo não cadastrado em DETRAN.VERSOES.")

            nu_versao_db, te_tag = row[0], str(row[1]).strip()
            cmp_res = compare_versions(local_version, nu_versao_db)

            if te_tag == "0":
                if cmp_res == 0:
                    return (True, f"Permitido: exige versão exata {nu_versao_db}.")
                else:
                    return (
                        False,
                        f"Negado: exige versão exata {nu_versao_db}, recebida {local_version}.",
                    )
            elif te_tag == "1":
                if cmp_res >= 0:
                    return (
                        True,
                        f"Permitido: versão mínima {nu_versao_db}, recebida {local_version} (ok).",
                    )
                else:
                    return (
                        False,
                        f"Negado: versão mínima {nu_versao_db}, recebida {local_version} (inferior).",
                    )
            else:
                return (False, f"Negado: TE_TAG inválido ({te_tag}).")
    finally:
        conn.close()


def _win_message_box_error(title: str, text: str) -> None:
    """Mostra MessageBox nativa (sem Tkinter), evitando conflitos com customtkinter."""
    try:
        import ctypes
        MB_ICONERROR = 0x10
        MB_OK = 0x0
        ctypes.windll.user32.MessageBoxW(0, text, title, MB_ICONERROR | MB_OK)
    except Exception:
        # fallback para console
        print(f"{title}: {text}", file=sys.stderr)


def gate_or_exit() -> None:
    """
    Chame no início do seu programa.
    - Não cria nenhum Tk; só usa MessageBox nativa se bloquear.
    - Evita erros "invalid command name ...after..." no customtkinter.
    """
    if getattr(sys, "frozen", False):
        exe_path = sys.executable
        fv = get_file_version_windows(exe_path)
        local_ver = fv if fv else LOCAL_VERSION
    else:
        local_ver = LOCAL_VERSION

    ok, msg = check_version_oracle(APP_NAME, local_ver)

    # Se versão não for permitida, bloqueia e fecha sem subir GUI
    if not ok:
        _win_message_box_error("Versão não permitida", msg)
        sys.exit(1)

    # Se permitido, opte por não exibir pop-ups aqui; apenas prossiga.
    # Se quiser log no console:
    print(msg)


if __name__ == "__main__":
    gate_or_exit()
    print("Liberação OK — continue a execução do app.")
