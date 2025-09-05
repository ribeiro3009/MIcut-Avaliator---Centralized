import pandas as pd
import oracledb
import re
from tqdm import tqdm

# Inicializa o modo "Thick" do driver Oracle para compatibilidade
oracledb.init_oracle_client()

# --- CONFIGURAÇÕES DO BANCO DE DADOS ORACLE ---
# PREENCHA ESTAS INFORMAÇÕES PARA CONECTAR AO SEU BANCO DE DADOS
ORACLE_USER = "seu_usuario_aqui"
ORACLE_PASSWORD = "sua_senha_aqui"
ORACLE_DSN = "host:porta/service_name"  # Ex: "192.168.1.50:1521/ORCL"

# Nomes das tabelas
TABLE_RECORTE = "FRC.RECORTE"

# Status padrão para todas as novas tarefas
STATUS_PENDENTE = 10

def extract_info(filename):
    """Extrai NU_PID e CO_DEDO do nome do arquivo."""
    if not isinstance(filename, str):
        return None, None
    
    pid_match = re.match(r'^(\d+)_.*'', filename)
    pid = int(pid_match.group(1)) if pid_match else None
    
    dedo_match = re.search(r'_dedo(\d+)', filename)
    dedo = int(dedo_match.group(1)) if dedo_match else None
    
    return pid, dedo

def import_data():
    """Lê o arquivo CSV e insere os dados na tabela FRC.RECORTE."""
    print("Lendo o arquivo Evaluation_biometry.csv...")
    try:
        df = pd.read_csv('Evaluation_biometry.csv')
    except FileNotFoundError:
        print("Erro: Arquivo 'Evaluation_biometry.csv' não encontrado.")
        return

    print("Processando dados do CSV para inserção na Tabela A (FRC.RECORTE)...")
    # Extrai PID e CO_DEDO
    df[['NU_PID', 'CO_DEDO']] = df['nome_imagem_digital'].apply(
        lambda x: pd.Series(extract_info(x))
    )

    # Remove linhas onde não foi possível extrair as informações
    df.dropna(subset=['NU_PID', 'CO_DEDO'], inplace=True)
    df['NU_PID'] = df['NU_PID'].astype(int)
    df['CO_DEDO'] = df['CO_DEDO'].astype(int)

    # Calcula largura e altura
    df['box_w'] = df['bbox_x2'] - df['bbox_x1']
    df['box_h'] = df['bbox_y2'] - df['bbox_y1']

    conn = None
    try:
        print(f"Conectando ao banco de dados Oracle em {ORACLE_DSN}...")
        conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
        cursor = conn.cursor()
        print("Conexão bem-sucedida. Iniciando a inserção de dados...")

        # Usar tqdm para barra de progresso
        for index, row in tqdm(df.iterrows(), total=df.shape[0], desc="Inserindo Dados em FRC.RECORTE"):
            sql_recorte = f"""
                INSERT INTO {TABLE_RECORTE} (NU_PID, CO_DEDO, TP_RECORTE_STATUS, FILENAME, BOX_X, BOX_Y, BOX_W, BOX_H)
                VALUES (:1, :2, :3, :4, :5, :6, :7, :8)
            """
            cursor.execute(sql_recorte, (
                row['NU_PID'],
                row['CO_DEDO'],
                STATUS_PENDENTE, # Todas as tarefas são inseridas como Pendentes
                row['nome_imagem_digital'],
                row['bbox_x1'],
                row['bbox_y1'],
                row['box_w'],
                row['box_h']
            ))
        
        conn.commit()
        print(f"\nImportação concluída com sucesso! {df.shape[0]} registros inseridos em {TABLE_RECORTE}.")

    except oracledb.DatabaseError as e:
        if conn: conn.rollback()
        print(f"\nErro de banco de dados durante a inserção: {e}")
    except Exception as e:
        if conn: conn.rollback()
        print(f"\nOcorreu um erro inesperado: {e}")
    finally:
        if conn:
            conn.close()
            print("Conexão com o banco de dados fechada.")

if __name__ == "__main__":
    import_data()
