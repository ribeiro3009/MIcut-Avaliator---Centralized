import ctypes
from ctypes import wintypes
import os

def test_senha_dll():
    """
    Script de teste para a função CalculaSenhaBD da Senha.dll.
    """
    try:
        dll_path = os.path.join("bin", "Senha.dll")

        if not os.path.exists(dll_path):
            print(f"ERRO: Arquivo Senha.dll não encontrado no caminho: {os.path.abspath(dll_path)}")
            print("Por favor, execute este script do diretório raiz do projeto (MIcut-Avaliator---Centralized).")
            return

        senha_dll = ctypes.CDLL(dll_path)
        print(f"DLL '{dll_path}' carregada com sucesso.")

        CalculaSenhaBD = senha_dll.CalculaSenhaBD
        
        # NOVA HIPÓTESE: A função recebe a senha e um buffer de saída.
        # void CalculaSenhaBD(const char* input, char* output_buffer);
        CalculaSenhaBD.argtypes = [wintypes.LPCSTR, wintypes.LPSTR]
        CalculaSenhaBD.restype = None  # Provavelmente não retorna nada (void)

        print("\n--- Teste da Função CalculaSenhaBD ---")
        
        password_to_test = input("Digite uma senha para testar a criptografia: ")

        # Cria um buffer para a função escrever o resultado.
        # 256 bytes é um tamanho seguro e comum para senhas criptografadas.
        output_buffer = ctypes.create_string_buffer(256)

        # Chama a função da DLL, passando a senha e o buffer
        CalculaSenhaBD(password_to_test.encode('ascii'), output_buffer)

        # O resultado agora está no buffer que passamos
        encrypted_password = output_buffer.value.decode('ascii')

        print("\n--- Resultados ---")
        print(f"Senha Original: {password_to_test}")
        print(f"Senha Criptografada: {encrypted_password}")
        print("\nTeste concluído com sucesso!")

    except FileNotFoundError:
        print(f"ERRO: Não foi possível encontrar o arquivo Senha.dll.")
        print("Certifique-se que a DLL está no diretório 'bin' dentro da pasta do projeto.")
    except AttributeError:
        print(f"ERRO: A função 'CalculaSenhaBD' não foi encontrada na DLL.")
    except Exception as e:
        print(f"Ocorreu um erro inesperado: {e}")

if __name__ == "__main__":
    test_senha_dll()