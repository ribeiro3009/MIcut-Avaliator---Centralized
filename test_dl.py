# test_dll.py
import ctypes
from ctypes import wintypes
import os, sys

def test_senha_dll():
    """
    Script de teste para a função CalculaSenhaBD da Senha.dll.
    """
    try:
        # Descobre a pasta-base (normal ou empacotado - sys._MEIPASS)
        BASE = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
        dll_dir = os.path.join(BASE, "bin")
        dll_path = os.path.join(dll_dir, "Senha.dll")  # atenção ao nome

        if not os.path.exists(dll_path):
            print(f"ERRO: Arquivo Senha.dll não encontrado no caminho: {os.path.abspath(dll_path)}")
            print("Execute a partir do diretório do projeto ou use o .exe gerado.")
            return

        # Ajuda o Windows a resolver dependências da Senha.dll (se houver)
        try:
            os.add_dll_directory(dll_dir)  # Python 3.8+
        except Exception:
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")

        # Carrega a DLL (se a DLL usar stdcall, prefira WinDLL)
        try:
            senha_dll = ctypes.WinDLL(dll_path)
        except Exception:
            senha_dll = ctypes.CDLL(dll_path)

        print(f"DLL '{dll_path}' carregada com sucesso.")

        CalculaSenhaBD = senha_dll.CalculaSenhaBD

        # Assinatura: void CalculaSenhaBD(const char* input, char* output_buffer);
        CalculaSenhaBD.argtypes = [wintypes.LPCSTR, wintypes.LPSTR]
        CalculaSenhaBD.restype = None

        print("\n--- Teste da Função CalculaSenhaBD ---")
        password_to_test = input("Digite uma senha para testar a criptografia: ")

        output_buffer = ctypes.create_string_buffer(256)
        CalculaSenhaBD(password_to_test.encode("ascii"), output_buffer)

        encrypted_password = output_buffer.value.decode("ascii", errors="ignore")

        print("\n--- Resultados ---")
        print(f"Senha Original: {password_to_test}")
        print(f"Senha Criptografada: {encrypted_password}")
        print("\nTeste concluído com sucesso!")

    except FileNotFoundError:
        print("ERRO: Não foi possível encontrar o arquivo Senha.dll.")
    except AttributeError:
        print("ERRO: A função 'CalculaSenhaBD' não foi encontrada na DLL.")
    except Exception as e:
        print(f"Ocorreu um erro inesperado: {e}")

if __name__ == "__main__":
    test_senha_dll()
