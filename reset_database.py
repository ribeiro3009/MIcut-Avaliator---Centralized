import oracledb
import sys

# --- CONFIGURAÇÕES DO BANCO DE DADOS ORACLE ---
# PREENCHA ESTAS INFORMAÇÕES PARA CONECTAR AO SEU BANCO DE DADOS
ORACLE_USER = "seu_usuario_aqui"
ORACLE_PASSWORD = "sua_senha_aqui"
ORACLE_DSN = "host:porta/service_name"  # Ex: "192.168.1.50:1521/ORCL"

# Nomes das tabelas e códigos de status
TABLE_RECORTE = "FRC.RECORTE"
TABLE_RECORTE_ANALISE = "FRC.RECORTE_ANALISE"
STATUS_PENDENTE = 10
STATUS_EM_PROCESSAMENTO = 20
STATUS_CONCLUIDO = 40

try:
    # Inicializa o modo "Thick" do driver Oracle para compatibilidade
    oracledb.init_oracle_client()
except oracledb.DatabaseError as e:
    print("Erro ao inicializar o Oracle Client. Verifique se ele está instalado e configurado corretamente.")
    print(f"Detalhe do erro: {e}")
    sys.exit(1)

def reset_database():
    """Reverte todas as tarefas para o estado 'Aguardando' e limpa a tabela de resultados."""
    conn = None
    try:
        conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
        cursor = conn.cursor()
        
        print("Iniciando transação de reset...")

        # 1. Reverte tarefas "Em Processamento" (20 -> 10)
        sql_update_proc = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :pendente WHERE TP_RECORTE_STATUS = :em_proc"
        cursor.execute(sql_update_proc, pendente=STATUS_PENDENTE, em_proc=STATUS_EM_PROCESSAMENTO)
        proc_rows = cursor.rowcount

        # 2. Reverte tarefas "Processado" (40 -> 10)
        sql_update_conc = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :pendente WHERE TP_RECORTE_STATUS = :concluido"
        cursor.execute(sql_update_conc, pendente=STATUS_PENDENTE, concluido=STATUS_CONCLUIDO)
        conc_rows = cursor.rowcount

        # 3. Limpa a tabela FRC.RECORTE_ANALISE
        sql_delete = f"DELETE FROM {TABLE_RECORTE_ANALISE}"
        cursor.execute(sql_delete)
        deleted_rows = cursor.rowcount
        
        conn.commit()
        print("\nReset concluído com sucesso!")
        print(f"- {proc_rows} tarefa(s) 'Em Processamento' revertida(s).")
        print(f"- {conc_rows} tarefa(s) 'Processado' revertida(s).")
        print(f"- {deleted_rows} registro(s) apagado(s) da tabela de análise.")

    except oracledb.DatabaseError as e:
        if conn: conn.rollback()
        print(f"\nERRO: A transação foi revertida. Nenhuma alteração foi salva.")
        print(f"Detalhe do erro de banco de dados: {e}")
    except Exception as e:
        if conn: conn.rollback()
        print(f"\nERRO: A transação foi revertida. Nenhuma alteração foi salva.")
        print(f"Detalhe do erro inesperado: {e}")
    finally:
        if conn:
            conn.close()
            print("\nConexão com o banco de dados fechada.")

if __name__ == "__main__":
    print("Executando script para resetar o ambiente de teste...")
    reset_database()
