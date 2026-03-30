import customtkinter as ctk
from tkinter import messagebox, simpledialog
import os
import pandas as pd
from PIL import Image, ImageTk, ImageDraw
import oracledb
import io
import socket
import datetime
import traceback
import threading
import sys
import platform
import ctypes
from ctypes import wintypes

from version_gate import gate_or_exit

#versão com escala de qualidade 0-9
# --- Bloco de Inicialização do Oracle Client ---
try:
    is_64bit = platform.architecture()[0] == "64bit"
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Rodando como um executável do PyInstaller
        client_folder = "instantclient_19_27" if is_64bit else "instantclient_12.2.0.1.0_x86"
        lib_dir = os.path.join(sys._MEIPASS, client_folder)
        oracledb.init_oracle_client(lib_dir=lib_dir)
    else:
        # Rodando como um script .py normal
        oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)
except oracledb.DatabaseError as e:
    messagebox.showerror("Erro Crítico de Banco de Dados",
                         f"Não foi possível inicializar o Oracle Client. Verifique a instalação e a arquitetura (32/64 bits).\n\nDetalhe: {e}")
    sys.exit(1)

# --- LÓGICA DE CRIPTOGRAFIA ---
def encrypt_password(password: str) -> tuple[str | None, str | None]:
    """
    Criptografa a senha usando a Senha.dll.

    Retorna uma tupla (encrypted_password, error_message).
    Se o sucesso, error_message é None.
    Se falhar, encrypted_password é None.
    """
    try:
        # O caminho para a DLL deve ser relativo ao executável
        # Se estiver rodando como script, o CWD deve ser a raiz do projeto
        #dll_path = os.path.join("bin", "Senha.dll")

        # Descobre a pasta-base (normal ou empacotado - sys._MEIPASS)
        BASE = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
        dll_dir = os.path.join(BASE, "bin")
        dll_path = os.path.join(dll_dir, "Senha.dll")  # atenção ao nome

        if not os.path.exists(dll_path):
            return None, f"Arquivo Senha.dll não encontrado em: {os.path.abspath(dll_path)}"

        senha_dll = ctypes.CDLL(dll_path)
        
        CalculaSenhaBD = senha_dll.CalculaSenhaBD
        CalculaSenhaBD.argtypes = [wintypes.LPCSTR, wintypes.LPSTR]
        CalculaSenhaBD.restype = None

        output_buffer = ctypes.create_string_buffer(256)
        
        CalculaSenhaBD(password.encode('ascii'), output_buffer)
        
        encrypted_password = output_buffer.value.decode('ascii')
        return encrypted_password, None

    except FileNotFoundError:
        return None, "Não foi possível encontrar o arquivo Senha.dll. Verifique a pasta 'bin'."
    except AttributeError:
        return None, "A função 'CalculaSenhaBD' não foi encontrada na Senha.dll."
    except Exception as e:
        return None, f"Erro inesperado na criptografia: {e}"

# --- CONFIGURAÇÕES GLOBAIS ---
IMAGE_FOLDER_PATH = r"\\imagens\Imagens\FRC_RECORTE"
ORACLE_DSN = ORACLE_DSN
# Nomes das tabelas
TABLE_RECORTE = "FRC.RECORTE"
TABLE_RECORTE_ANALISE = "FRC.RECORTE_ANALISE"
TABLE_RECORTE_STATUS = "FRC.RECORTE_STATUS"
TABLE_OPERADORES ="DETRAN.OPERADORES"

# Códigos de status
STATUS_PENDENTE = 10
STATUS_EM_PROCESSAMENTO = 20
STATUS_CONCLUIDO = 40

try:
    hostname = socket.gethostname()
    numeric_id_str = "".join(filter(str.isdigit, hostname))
    OPERATOR_ID = int(numeric_id_str) if numeric_id_str else 0
except:
    OPERATOR_ID = 0

# --- LÓGICA DE NEGÓCIO E DADOS (ORACLE DB) ---

class TaskManager:
    def __init__(self, user, password, dsn):
        """Inicializa o TaskManager e estabelece conexão com o Oracle DB."""
        self.conn = None
        self.user = user
        self.password = password
        self.dsn = dsn
        try:
            self.conn = oracledb.connect(user=self.user, password=self.password, dsn=self.dsn)
            with self.conn.cursor() as cursor:
                cursor.execute(f"SELECT 1 FROM {TABLE_RECORTE} WHERE 1=0")
                cursor.execute(f"SELECT 1 FROM {TABLE_RECORTE_ANALISE} WHERE 1=0")
        except oracledb.DatabaseError as e:
            messagebox.showerror("Erro de Banco de Dados", f"Não foi possível conectar ao Oracle DB: {e}\n\nVerifique suas credenciais, o DSN e a conexão de rede.")
            raise
        except Exception as e:
            messagebox.showerror("Erro Crítico na Inicialização", f"Ocorreu um erro inesperado: {e}")
            raise

    def __del__(self):
        """Garante que a conexão com o banco de dados seja fechada."""
        if self.conn:
            self.conn.close()

    def fetch_and_reserve_batch(self, batch_size: int):
        """Busca um lote de tarefas, reservando-as no banco de dados de forma atômica."""
        try:
            with self.conn.cursor() as cursor:
                self.conn.autocommit = False

                # 1. Busca todas as tarefas pendentes da tabela de recortes
                sql_fetch_pending = f"""
                    SELECT
                        NU_PID, CO_DEDO, FILENAME,
                        BOX_X, BOX_Y, BOX_W, BOX_H
                    FROM
                        {TABLE_RECORTE}
                    WHERE
                        TP_RECORTE_STATUS = :status_pendente
                """
                cursor.execute(sql_fetch_pending, status_pendente=STATUS_PENDENTE)
                
                db_columns = [desc[0].lower() for desc in cursor.description]
                pending_df = pd.DataFrame(cursor.fetchall(), columns=db_columns)

                if pending_df.empty:
                    return None, "Todas as avaliações foram concluídas!"

                # 2. Renomeia e calcula colunas para manter a compatibilidade com a UI
                rename_map = {
                    'nu_pid': 'nu_pid',
                    'co_dedo': 'co_dedo',
                    'filename': 'nome_imagem_digital',
                    'box_x': 'bbox_x1',
                    'box_y': 'bbox_y1',
                    'box_w': 'box_w',
                    'box_h': 'box_h'
                }
                pending_df.rename(columns=rename_map, inplace=True)
                # Calcula as coordenadas x2, y2 a partir da largura e altura lidas do banco
                pending_df['bbox_x2'] = pending_df['bbox_x1'] + pending_df['box_w']
                pending_df['bbox_y2'] = pending_df['bbox_y1'] + pending_df['box_h']
                
                # A chave de uma tarefa agora é composta por (nu_pid, co_dedo)
                pending_df.set_index(['nu_pid', 'co_dedo'], inplace=True)

                # 3. Agrupa tarefas por "mão" (hand), replicando a lógica original
                def get_person_hand_id(image_name):
                    try:
                        parts = os.path.splitext(str(image_name))[0].split('_')
                        person_id = parts[0]
                        finger_str = parts[1]
                        finger_num = int("".join(filter(str.isdigit, finger_str)))
                        hand_name = "hand1" if 1 <= finger_num <= 5 else "hand2"
                        return f"{person_id}_{hand_name}"
                    except (IndexError, ValueError):
                        return None
                
                pending_df['person_hand_id'] = pending_df['nome_imagem_digital'].apply(get_person_hand_id)
                pending_df.dropna(subset=['person_hand_id'], inplace=True)

                unique_hand_ids = pending_df['person_hand_id'].unique()
                if len(unique_hand_ids) == 0:
                    return None, "Não foi possível encontrar tarefas pendentes com formato de nome válido."

                # 4. Seleciona o lote de mãos e reserva as tarefas
                batch_hand_ids = unique_hand_ids[:batch_size]
                tasks_to_reserve_df = pending_df[pending_df['person_hand_id'].isin(batch_hand_ids)].copy()
                task_ids_to_reserve = tasks_to_reserve_df.index.tolist() # Lista de tuplas (nu_pid, co_dedo)

                if not task_ids_to_reserve:
                    return None, "Nenhuma tarefa encontrada para o lote selecionado."

                # 5. Bloqueia e atualiza as linhas na Tabela de Recorte
                # A sintaxe de comparação de tuplas pode variar. Esta é comum em Oracle.
                id_placeholders = ", ".join([f"(:pid{i}, :cid{i})" for i in range(len(task_ids_to_reserve))])
                lock_sql = f"SELECT NU_PID FROM {TABLE_RECORTE} WHERE (NU_PID, CO_DEDO) IN ({id_placeholders}) AND TP_RECORTE_STATUS = :status_pendente FOR UPDATE"
                
                bind_vars = {'status_pendente': STATUS_PENDENTE}
                for i, (pid, cid) in enumerate(task_ids_to_reserve):
                    bind_vars[f'pid{i}'] = pid
                    bind_vars[f'cid{i}'] = cid
                
                cursor.execute(lock_sql, bind_vars)
                
                locked_rows = cursor.fetchall()
                if len(locked_rows) != len(task_ids_to_reserve):
                    self.conn.rollback()
                    return None, "Conflito de reserva. Outro operador pode ter pego estas tarefas. Tente novamente."

                update_sql = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :status_proc WHERE NU_PID = :pid AND CO_DEDO = :cid"
                update_data = [{'status_proc': STATUS_EM_PROCESSAMENTO, 'pid': pid, 'cid': cid} for pid, cid in task_ids_to_reserve]
                cursor.executemany(update_sql, update_data)
                
                self.conn.commit()

                # 6. Formata os dados para a interface gráfica (lógica inalterada)
                unique_hands = []
                for hand_id in batch_hand_ids:
                    hand_tasks_df = tasks_to_reserve_df[tasks_to_reserve_df['person_hand_id'] == hand_id]
                    # O índice agora é um MultiIndex, mas a lógica de agrupar por 'person_hand_id' continua a mesma
                    unique_hands.append({
                        "id": hand_id,
                        "image_name": f"column_{hand_id}.png",
                        "tasks": hand_tasks_df
                    })
                
                return unique_hands, None

        except oracledb.DatabaseError as e:
            if self.conn: self.conn.rollback()
            return None, f"Erro no banco de dados durante a busca: {e}"
        finally:
            if self.conn: self.conn.autocommit = True


    def process_image_batch(self, hands_to_process, progress_callback):
        """Carrega as imagens das mãos a serem processadas. (Lógica inalterada)"""
        processed_image = {}
        total_hands = len(hands_to_process)

        if not os.path.isdir(IMAGE_FOLDER_PATH):
            messagebox.showerror("Erro de Configuração", f"O caminho das imagens não foi encontrado ou não é uma pasta válida:\n{IMAGE_FOLDER_PATH}")
            return {}

        for i, hand in enumerate(hands_to_process):
            image_name = hand["image_name"]
            progress_callback(f"Carregando: {image_name} ({i+1}/{total_hands})")
            
            full_image_path = os.path.join(IMAGE_FOLDER_PATH, image_name)
            
            try:
                if not os.path.exists(full_image_path):
                    raise FileNotFoundError(f"Imagem '{image_name}' não encontrada em:\n{full_image_path}")

                image = Image.open(full_image_path)
                processed_image[hand["id"]] = image

            except FileNotFoundError as e:
                messagebox.showerror("Erro ao Carregar Imagem", str(e))
                processed_image[hand["id"]] = None
            except Exception as e:
                messagebox.showerror("Erro Inesperado", f"Falha ao carregar {image_name}: {e}")
                processed_image[hand["id"]] = None
        
        return processed_image

    def update_batch_results(self, evaluated_tasks, unevaluated_tasks, user_id=None):
        """Atualiza os resultados do lote no banco de dados, inserindo em RECORTE_ANALISE e atualizando status em RECORTE."""
        try:
            with self.conn.cursor() as cursor:
                self.conn.autocommit = False
                timestamp = datetime.datetime.now()

                # 1. Tarefas concluídas: INSERIR em RECORTE_ANALISE e ATUALIZAR em RECORTE
                if not evaluated_tasks.empty:
                    # Insere os resultados na tabela de análise
                    sql_analise = f"""
                        INSERT INTO {TABLE_RECORTE_ANALISE} (NU_PID, CO_DEDO, FATOR_1_RECORTE_CORRETO, FATOR_2_QUALIDADE_SUFICIENTE, NU_RICOPER, DT_TRATPEDIDO)
                        VALUES (:pid, :cid, :f1, :f2, :op_id, :ts)
                    """
                    # O driver do oracledb lida com a conversão do objeto datetime do Python diretamente
                    op_id_value = user_id if user_id is not None else OPERATOR_ID
                    eval_data_insert = [
                        {
                            'pid': int(index[0]),
                            'cid': int(index[1]),
                            'f1': 1 if row['fator_1_recorte_correto'] == 'SIM' else 0,
                            'f2': int(row['fator_2_qualidade_suficiente']) - 1,
                            'op_id': op_id_value, # Mapeado para NU_RICOPER
                            'ts': timestamp      # Mapeado para DT_TRATPEDIDO
                        }
                        for index, row in evaluated_tasks.iterrows()
                    ]
                    cursor.executemany(sql_analise, eval_data_insert)

                    # Atualiza o status na tabela de recorte para CONCLUIDO
                    update_sql_recorte = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :status WHERE NU_PID = :pid AND CO_DEDO = :cid"
                    eval_data_update = [
                        {
                            'status': STATUS_CONCLUIDO,
                            'pid': int(index[0]),
                            'cid': int(index[1])
                        }
                        for index, row in evaluated_tasks.iterrows()
                    ]
                    cursor.executemany(update_sql_recorte, eval_data_update)

                # 2. Tarefas não concluídas (liberar): ATUALIZAR status em RECORTE para PENDENTE
                if not unevaluated_tasks.empty:
                    update_uneval_sql = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :status WHERE NU_PID = :pid AND CO_DEDO = :cid"
                    uneval_data = [
                        {
                            'status': STATUS_PENDENTE,
                            'pid': int(index[0]),
                            'cid': int(index[1])
                        }
                        for index, row in unevaluated_tasks.iterrows()
                    ]
                    cursor.executemany(update_uneval_sql, uneval_data)

                self.conn.commit()
                return True
        except oracledb.DatabaseError as e:
            if self.conn: self.conn.rollback()
            messagebox.showerror("Erro de Banco de Dados", f"Falha ao salvar resultados: {e}")
            return False
        finally:
            if self.conn: self.conn.autocommit = True

# --- INTERFACE GRÁFICA ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MIcut Avaliator - Centralizado")
        self.task_manager = None
        self.current_screen = None
        self.operator_name = None  # Novo atributo para armazenar o nome do operador
        self.show_login_screen()

    def show_screen(self, screen_class, *args, **kwargs):
        if self.current_screen:
            self.current_screen.destroy()
        
        geometry = kwargs.pop("geometry", "500x300") # Smaller default geometry
        resizable = kwargs.pop("resizable", False)
        self.geometry(geometry)
        self.resizable(resizable, resizable)

        self.current_screen = screen_class(self, *args, **kwargs)
        self.current_screen.pack(expand=True, fill="both")

    def on_login_success(self, user, password, user_id=None):
        try:
            self.task_manager = TaskManager(user, password, ORACLE_DSN)
            self.logged_user_id = user_id  # Salva o user_id para uso posterior
            operator_name = user_id  # fallback para o próprio user_id
            if user_id:
                try:
                    with self.task_manager.conn.cursor() as cursor:
                        sql = f"SELECT NO_OPERADOR FROM {TABLE_OPERADORES} WHERE NU_RICOPER = :user_id"
                        cursor.execute(sql, user_id=user_id)
                        result = cursor.fetchone()
                        if result and result[0]:
                            operator_name = result[0]
                except Exception as e:
                    print(f"Aviso: Não foi possível buscar o nome do operador. Usando o próprio user_id. Erro: {e}")
            self.operator_name = operator_name  # Salva para uso futuro
            self.show_batch_selection_screen(operator_name)
        except oracledb.DatabaseError as e:
            self.show_login_screen(error=f"Falha na conexão: {e}")
        except Exception as e:
            self.show_error_screen(f"Falha na inicialização: {e}")

    def show_login_screen(self, error=None):
        self.show_screen(LoginScreen, on_login=self.on_login_success, error=error, geometry="500x300", resizable=False)

    def show_batch_selection_screen(self, operator_name=None):
        if operator_name is None:
            operator_name = getattr(self, 'operator_name', None)
        if not operator_name:
            operator_name = "Operador"
        self.show_screen(BatchSelectionScreen, operator_name=operator_name, geometry="500x300", resizable=False)

    def show_loading_screen(self, batch_size):
        self.show_screen(LoadingScreen, batch_size=batch_size, geometry="500x200", resizable=False)

    def show_evaluation_screen(self, batch_data, processed_image):
        self.show_screen(EvaluationScreen, batch_data=batch_data, processed_image=processed_image, user_id=self.logged_user_id, geometry="1200x800", resizable=True)
        self.protocol("WM_DELETE_WINDOW", self.current_screen.on_closing)

    def show_error_screen(self, message):
        self.show_screen(ErrorScreen, message=message)

class LoginScreen(ctk.CTkFrame):
    def __init__(self, master, on_login, error=None):
        super().__init__(master)
        self.master = master
        self.on_login = on_login

        ctk.CTkLabel(self, text="Login", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(20,10))
        
        ctk.CTkLabel(self, text="Usuário:").pack(pady=(10,0), padx=20, anchor="w")
        self.user_entry = ctk.CTkEntry(self, width=300)
        self.user_entry.pack(pady=5, padx=20)

        ctk.CTkLabel(self, text="Senha:").pack(pady=(10,0), padx=20, anchor="w")
        self.password_entry = ctk.CTkEntry(self, show="*", width=300)
        self.password_entry.pack(pady=5, padx=20)

        self.login_button = ctk.CTkButton(self, text="Login", command=self.attempt_login, width=300)
        self.login_button.pack(pady=20, ipady=10)

        self.status_label = ctk.CTkLabel(self, text=error or "", text_color="red")
        self.status_label.pack(pady=5)

        self.user_entry.bind("<Return>", lambda event: self.attempt_login())
        self.password_entry.bind("<Return>", lambda event: self.attempt_login())

    def attempt_login(self):
        user_id = self.user_entry.get()
        user = "rj" + user_id.zfill(10)
        plain_password = self.password_entry.get()
        if not user or not plain_password: messagebox.showerror("Erro de Login", "Usuário e senha não podem estar em branco."); return

        self.status_label.configure(text="Criptografando e conectando...", text_color="#FFFFFF")
        self.login_button.configure(state="disabled")
        self.update() # Força a atualização da UI

        encrypted_password, error = encrypt_password(plain_password)

        if error:
            self.status_label.configure(text=error, text_color="red")
            self.login_button.configure(state="normal")
            return

        # Chama o callback para a classe App tentar a conexão, passando também o user_id numérico
        self.master.after(100, lambda: self.on_login(user, encrypted_password, user_id))

class BatchSelectionScreen(ctk.CTkFrame):
    def __init__(self, master, operator_name):
        super().__init__(master)
        self.master = master

        ctk.CTkLabel(self, text="MIcut Avaliator", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(20,10))
        ctk.CTkLabel(self, text=f"Operador: {operator_name}").pack(pady=5)
        
        ctk.CTkLabel(self, text="Quantas mãos você vai avaliar nesta sessão?").pack(pady=(20,5))
        self.batch_size_entry = ctk.CTkEntry(self, justify="center")
        self.batch_size_entry.pack(pady=5)
        self.batch_size_entry.insert(0, "10")


        ctk.CTkButton(self, text="Iniciar Avaliação", command=self.start_evaluation).pack(pady=20, ipady=10)

        self.batch_size_entry.bind("<Return>", lambda event: self.start_evaluation())
    def start_evaluation(self):
        try:
            batch_size = int(self.batch_size_entry.get())
            if batch_size <= 0:
                raise ValueError
            self.master.show_loading_screen(batch_size)
        except (ValueError, TypeError):
            messagebox.showerror("Entrada Inválida", "Por favor, insira um número inteiro positivo.")

class LoadingScreen(ctk.CTkFrame):
    def __init__(self, master, batch_size):
        super().__init__(master)
        self.master = master
        self.batch_size = batch_size

        self.progress_label = ctk.CTkLabel(self, text="Buscando e reservando tarefas...", font=ctk.CTkFont(size=14))
        self.progress_label.pack(pady=(40, 10))
        self.progress_bar = ctk.CTkProgressBar(self, mode='indeterminate')
        self.progress_bar.pack(pady=10, padx=20, fill="x")
        self.progress_bar.start()

        self.after(100, self.load_data)

    def load_data(self):
        threading.Thread(target=self._load_data_thread).start()

    def _load_data_thread(self):
        batch_data, error = self.master.task_manager.fetch_and_reserve_batch(self.batch_size)
        if error:
            # Como estamos em outra thread, precisamos agendar a chamada para a thread principal da UI
            self.master.after(0, lambda: messagebox.showerror("Erro", error))
            # Recupera o nome do operador da tela anterior (BatchSelectionScreen)
            operator_name = None
            if hasattr(self.master.current_screen, 'operator_name'):
                operator_name = getattr(self.master.current_screen, 'operator_name', None)
            if not operator_name:
                operator_name = getattr(self.master, 'operator_name', None)
            if not operator_name:
                operator_name = "Operador"
            self.master.after(0, lambda: self.master.show_batch_selection_screen(operator_name))
            return

        def progress_callback(message):
            # Agendar atualização do label na thread da UI
            self.master.after(0, self.progress_label.configure, {"text": message})

        processed_image = self.master.task_manager.process_image_batch(batch_data, progress_callback)
        self.master.after(0, self.on_loading_complete, batch_data, processed_image)

    def on_loading_complete(self, batch_data, processed_image):
        self.master.show_evaluation_screen(batch_data, processed_image)

class EvaluationScreen(ctk.CTkFrame):
    def __init__(self, master, batch_data, processed_image, user_id=None):
        super().__init__(master)
        self.master = master
        self.task_manager = master.task_manager
        self.batch_data = batch_data
        self.processed_image = processed_image
        self.user_id = user_id
        self.current_task_index = 0
        # O DataFrame de resultados usará o ID da tarefa (do DB) como índice
        self.results = pd.DataFrame()
        self.middle_column_visible = False
        self.finger_widgets = {}
        self.finger_order = []
        self.active_finger_pos = 0
        self._key_binds = []
        self.ctk_col_img = None

        self.setup_ui()
        self.bind_shortcuts()
        self.load_current_task()

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=3)
        self.grid_rowconfigure(0, weight=1)

        actions_frame = ctk.CTkFrame(self, width=200)
        actions_frame.grid(row=0, column=0, sticky="nswe", padx=10, pady=10)
        actions_frame.grid_propagate(False)

        self.progress_label = ctk.CTkLabel(actions_frame, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.progress_label.pack(pady=10, padx=10)

        self.finger_progress_label = ctk.CTkLabel(actions_frame, text="", font=ctk.CTkFont(size=13))
        self.finger_progress_label.pack(pady=(0, 10), padx=10)

        self.toggle_col_button = ctk.CTkButton(
            actions_frame,
            text="Mostrar Coluna Inteira",
            command=self.toggle_middle_column,
        )
        self.toggle_col_button.pack(side="bottom", pady=10, padx=20, fill="x")

        self.next_button = ctk.CTkButton(actions_frame, text="Salvar", command=self.next_task)
        self.next_button.pack(side="bottom", pady=10, padx=20, fill="x", ipady=10)

        self.save_exit_button = ctk.CTkButton(actions_frame, text="Sair", command=self.on_closing)
        self.save_exit_button.pack(side="bottom", pady=10, padx=20, fill="x")

        self.col_frame = ctk.CTkFrame(self)
        self.col_frame.grid(row=0, column=1, sticky="nswe", padx=(0, 10), pady=10)
        self.col_image_label = ctk.CTkLabel(self.col_frame, text="")
        self.col_image_label.pack(expand=True, fill="both", padx=5, pady=5)
        self.col_frame.grid_remove()

        self.crops_frame = ctk.CTkScrollableFrame(self, label_text="Recortes para Avaliação")
        self.crops_frame.grid(row=0, column=2, sticky="nswe", padx=(0, 10), pady=10)

    def bind_shortcuts(self):
        bindings = {
            "<Right>": self.on_next_key,
            "<Return>": self.on_next_key,
            "<Left>": self.on_prev_key,
            "<s>": lambda e: self.set_current_f1("SIM"),
            "<S>": lambda e: self.set_current_f1("SIM"),
            "<n>": lambda e: self.set_current_f1("NAO"),
            "<N>": lambda e: self.set_current_f1("NAO"),
            "<Key-1>": lambda e: self.set_current_quality(1),
            "<Key-2>": lambda e: self.set_current_quality(2),
            "<Key-3>": lambda e: self.set_current_quality(3),
            "<Key-4>": lambda e: self.set_current_quality(4),
            "<Key-5>": lambda e: self.set_current_quality(5),
        }
        for sequence, callback in bindings.items():
            self.master.bind(sequence, callback)
            self._key_binds.append(sequence)

    def destroy(self):
        for sequence in self._key_binds:
            self.master.unbind(sequence)
        super().destroy()

    def toggle_middle_column(self):
        self.middle_column_visible = not self.middle_column_visible
        if self.middle_column_visible:
            self.col_frame.grid()
            self.grid_columnconfigure(1, weight=1)
            self.grid_columnconfigure(2, weight=2)
            self.toggle_col_button.configure(text="Ocultar Coluna Inteira")
        else:
            self.col_frame.grid_remove()
            self.grid_columnconfigure(1, weight=0)
            self.grid_columnconfigure(2, weight=3)
            self.toggle_col_button.configure(text="Mostrar Coluna Inteira")

    def load_current_task(self):
        for widget in self.crops_frame.winfo_children():
            widget.destroy()

        current_hand = self.batch_data[self.current_task_index]
        self.current_task_df = current_hand["tasks"].copy().sort_index(level='co_dedo')
        self.finger_widgets = {}
        self.finger_order = list(self.current_task_df.index)
        self.active_finger_pos = 0

        self.cards_row_frame = ctk.CTkFrame(self.crops_frame, fg_color="transparent")
        self.cards_row_frame.pack(fill="both", expand=True, padx=5, pady=5)
        total_fingers = max(1, len(self.current_task_df))
        for col in range(total_fingers):
            self.cards_row_frame.grid_columnconfigure(col, weight=1, uniform="finger_col")

        self.progress_label.configure(text=f"Avaliando Mão {self.current_task_index + 1} de {len(self.batch_data)}")
        if self.current_task_index == len(self.batch_data) - 1:
            self.next_button.configure(text="Finalizar e Salvar Tud  ", fg_color="green")
        else:
            self.next_button.configure(text="Salvar", fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"])

        original_img = self.processed_image.get(current_hand["id"])
        if original_img is None:
            self.col_image_label.configure(text=f"Imagem para\n{current_hand['id']}\nnão foi carregada.", image=None)
            return

        display_img = original_img.copy()
        draw = ImageDraw.Draw(display_img, "RGBA")

        for position, (df_index, row) in enumerate(self.current_task_df.iterrows()):
            box = (int(row['bbox_x1']), int(row['bbox_y1']), int(row['bbox_x2']), int(row['bbox_y2']))
            draw.rectangle(box, outline="red", width=3)
            crop_image_obj = original_img.crop(box)
            self.create_crop_widget(df_index, row, crop_image_obj, position)

        max_w = 550
        max_h = 750
        original_w, original_h = display_img.size
        if original_w > max_w or original_h > max_h:
            ratio = min(max_w / original_w, max_h / original_h)
            new_w = int(original_w * ratio)
            new_h = int(original_h * ratio)
            display_img = display_img.resize((new_w, new_h), Image.LANCZOS)

        self.ctk_col_img = ctk.CTkImage(light_image=display_img, size=display_img.size)
        self.col_image_label.configure(image=self.ctk_col_img, text="")

        self.focus_set()
        self.set_active_finger(self.first_incomplete_finger_pos())

    def create_crop_widget(self, df_index, data_row, crop_image: Image.Image, position):
        widget_frame = ctk.CTkFrame(self.cards_row_frame, border_width=0)
        widget_frame.grid(row=0, column=position, sticky="nsew", padx=5, pady=5)
        widget_frame.grid_columnconfigure(0, weight=1)

        rotated_crop = crop_image.rotate(90, expand=True)
        original_w, original_h = rotated_crop.size
        max_w = 360
        max_h = 320
        ratio = min(max_w / max(1, original_w), max_h / max(1, original_h))
        ratio = min(ratio, 1.25)
        new_w = max(1, int(original_w * ratio))
        new_h = max(1, int(original_h * ratio))
        displayed_crop = rotated_crop.resize((new_w, new_h), Image.LANCZOS)

        ctk_crop_img = ctk.CTkImage(light_image=displayed_crop, size=(new_w, new_h))
        img_label = ctk.CTkLabel(widget_frame, image=ctk_crop_img, text="")
        img_label.bind("<Button-1>", lambda e, img=rotated_crop: ZoomWindow(self, img))
        img_label.grid(row=0, column=0, padx=8, pady=(8, 6), sticky="w")
        img_label._ctk_img_ref = ctk_crop_img

        ctk.CTkLabel(
            widget_frame,
            text=data_row['nome_imagem_digital'],
            font=ctk.CTkFont(weight="bold")
        ).grid(row=1, column=0, sticky="w", padx=8)

        quality_var = ctk.IntVar(value=0)
        f1_var = ctk.StringVar(value=str(data_row.get('fator_1_recorte_correto', '')))

        quality_frame = ctk.CTkFrame(widget_frame, fg_color="transparent")
        quality_frame.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 2))
        quality_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(quality_frame, text="Qualidade:").grid(row=0, column=0, sticky="w")

        quality_buttons_frame = ctk.CTkFrame(quality_frame, fg_color="transparent")
        quality_buttons_frame.grid(row=1, column=0, sticky="w", pady=(2, 0))

        quality_buttons = {}
        for idx, label in enumerate(["?", "1", "2", "3", "4", "5"]):
            value = 0 if label == "?" else int(label)
            btn = ctk.CTkRadioButton(
                quality_buttons_frame,
                text=label,
                variable=quality_var,
                value=value,
                width=38,
                command=lambda i=df_index: self.set_active_by_index(i),
            )
            btn.grid(row=0, column=idx, padx=(0, 6), sticky="w")
            quality_buttons[value] = btn

        f1_frame = ctk.CTkFrame(widget_frame, fg_color="transparent")
        f1_frame.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 8))
        ctk.CTkLabel(f1_frame, text="Recorte correto?").pack(side="left")

        no_btn = ctk.CTkRadioButton(
            f1_frame,
            text="Não",
            variable=f1_var,
            value="NAO",
            command=lambda i=df_index: self.set_active_by_index(i),
        )
        no_btn.pack(side="right")

        yes_btn = ctk.CTkRadioButton(
            f1_frame,
            text="Sim",
            variable=f1_var,
            value="SIM",
            command=lambda i=df_index: self.set_active_by_index(i),
        )
        yes_btn.pack(side="right", padx=8)

        self.current_task_df.loc[df_index, 'f1_var'] = f1_var
        self.current_task_df.loc[df_index, 'f2_var'] = quality_var
        self.finger_widgets[df_index] = {
            'frame': widget_frame,
            'quality_var': quality_var,
            'f1_var': f1_var,
            'quality_buttons': quality_buttons,
            'yes_btn': yes_btn,
            'no_btn': no_btn,
        }

    def first_incomplete_finger_pos(self):
        for pos, idx in enumerate(self.finger_order):
            row = self.current_task_df.loc[idx]
            if not row['f1_var'].get() or row['f2_var'].get() == 0:
                return pos
        return 0

    def set_active_by_index(self, df_index):
        if df_index in self.finger_order:
            self.set_active_finger(self.finger_order.index(df_index))

    def set_active_finger(self, pos):
        if not self.finger_order:
            self.finger_progress_label.configure(text="")
            return

        pos = max(0, min(pos, len(self.finger_order) - 1))
        self.active_finger_pos = pos
        active_index = self.finger_order[pos]

        for idx, data in self.finger_widgets.items():
            if idx == active_index:
                data['frame'].configure(border_width=2, border_color="#2FA572")
            else:
                data['frame'].configure(border_width=0)

        self.finger_progress_label.configure(text=f"Dedo {self.active_finger_pos + 1} de {len(self.finger_order)}")
        self.scroll_active_into_view()

    def scroll_active_into_view(self):
        if not self.finger_order or not hasattr(self.crops_frame, "_parent_canvas"):
            return

        active_index = self.finger_order[self.active_finger_pos]
        frame = self.finger_widgets[active_index]['frame']
        canvas = self.crops_frame._parent_canvas

        self.update_idletasks()
        frame_y = frame.winfo_y()
        content_h = self.crops_frame.winfo_height()
        canvas_h = canvas.winfo_height()
        scroll_max = max(1, content_h - canvas_h)
        canvas.yview_moveto(max(0.0, min(1.0, frame_y / scroll_max)))

    def current_finger_index(self):
        if not self.finger_order:
            return None
        return self.finger_order[self.active_finger_pos]

    def set_current_quality(self, value):
        current_idx = self.current_finger_index()
        if current_idx is None:
            return "break"
        self.current_task_df.loc[current_idx, 'f2_var'].set(value)
        self.set_active_by_index(current_idx)
        return "break"

    def set_current_f1(self, value):
        current_idx = self.current_finger_index()
        if current_idx is None:
            return "break"
        self.current_task_df.loc[current_idx, 'f1_var'].set(value)
        self.set_active_by_index(current_idx)
        return "break"

    def validate_finger(self, df_index, show_message=True):
        row = self.current_task_df.loc[df_index]
        if not row['f1_var'].get() or row['f2_var'].get() == 0:
            if show_message:
                messagebox.showwarning("Atenção", "Defina Sim/Não e a nota de 1 a 5 do dedo ativo antes de avançar.")
            return False
        return True

    def on_next_key(self, event=None):
        if not self.finger_order:
            return "break"

        current_idx = self.current_finger_index()
        if current_idx is None:
            return "break"

        if not self.validate_finger(current_idx, show_message=True):
            return "break"

        if self.active_finger_pos < len(self.finger_order) - 1:
            self.set_active_finger(self.active_finger_pos + 1)
        else:
            self.next_task()
        return "break"

    def on_prev_key(self, event=None):
        if self.active_finger_pos > 0:
            self.set_active_finger(self.active_finger_pos - 1)
        return "break"

    def next_task(self):
        for index, row in self.current_task_df.iterrows():
            if not row['f1_var'].get() or row['f2_var'].get() == 0:
                messagebox.showwarning("Atenção", "Por favor, avalie todos os dedos (Sim/Não e qualidade 1 a 5).")
                return

        for index, row in self.current_task_df.iterrows():
            self.current_task_df.loc[index, 'fator_1_recorte_correto'] = row['f1_var'].get()
            self.current_task_df.loc[index, 'fator_2_qualidade_suficiente'] = row['f2_var'].get()

        self.results = pd.concat([self.results, self.current_task_df.drop(columns=['f1_var', 'f2_var'])])

        if self.current_task_index < len(self.batch_data) - 1:
            self.current_task_index += 1
            self.load_current_task()
        else:
            self.on_closing(final_save=True)

    def on_closing(self, final_save=False):
        if not final_save:
            if not messagebox.askyesno("Confirmar Saída", "Tem certeza que deseja sair? Apenas as MÃOS 100% concluídas serão salvas. O progresso em mãos parcialmente avaliadas será perdido."):
                return

        # Lógica para salvar apenas mãos completas
        all_tasks_df = pd.concat([hand["tasks"] for hand in self.batch_data])

        completed_hand_ids = []
        if not self.results.empty:
            # Agrupa os dedos avaliados por 'mão'
            evaluated_fingers_by_hand = self.results.groupby('person_hand_id')

            for hand_data in self.batch_data:
                hand_id = hand_data["id"]
                total_fingers_in_hand = len(hand_data["tasks"])
                
                # Verifica se a mão teve algum dedo avaliado
                if hand_id in evaluated_fingers_by_hand.groups:
                    evaluated_count = len(evaluated_fingers_by_hand.get_group(hand_id))
                    # Se todos os dedos da mão foram avaliados, marca a mão como completa
                    if evaluated_count == total_fingers_in_hand:
                        completed_hand_ids.append(hand_id)

        # Separa os dataframes com base nas mãos completas
        tasks_to_save_df = all_tasks_df[all_tasks_df['person_hand_id'].isin(completed_hand_ids)]
        tasks_to_release_df = all_tasks_df[~all_tasks_df['person_hand_id'].isin(completed_hand_ids)]

        # Adiciona os resultados da avaliação ao dataframe que será salvo
        if not tasks_to_save_df.empty:
            # Garante que o dataframe de resultados tenha o mesmo tipo de índice que o de tarefas
            results_to_merge = self.results.reset_index().set_index(['nu_pid', 'co_dedo'])
            tasks_to_save_df = tasks_to_save_df.merge(
                results_to_merge[['fator_1_recorte_correto', 'fator_2_qualidade_suficiente']],
                left_index=True,
                right_index=True,
                how='left'
            )

        self.task_manager.update_batch_results(tasks_to_save_df, tasks_to_release_df, self.user_id)
        
        if final_save:
            messagebox.showinfo("Sucesso", "Lote de avaliação concluído e salvo com sucesso!")
        else:
            messagebox.showinfo("Progresso Salvo", "Seu progresso foi salvo. Tarefas de mãos incompletas foram liberadas.")

        self.master.protocol("WM_DELETE_WINDOW", self.master.destroy)
        # Recupera o nome do operador da tela anterior (BatchSelectionScreen)
        operator_name = None
        if hasattr(self.master.current_screen, 'operator_name'):
            operator_name = getattr(self.master.current_screen, 'operator_name', None)
        if not operator_name:
            operator_name = getattr(self.master, 'operator_name', None)
        if not operator_name:
            operator_name = "Operador"
        self.master.show_batch_selection_screen(operator_name)

class ErrorScreen(ctk.CTkFrame):
    def __init__(self, master, message):
        super().__init__(master)
        ctk.CTkLabel(self, text="Ocorreu um erro irrecuperável:", text_color="red").pack(pady=(20,5))
        ctk.CTkLabel(self, text=message, wraplength=480).pack(pady=5, padx=10)

class ZoomWindow(ctk.CTkToplevel):
    def __init__(self, master, image: Image.Image):
        super().__init__(master)
        self.title("Zoom (Scroll para zoom, clique e arraste para mover)")
        self.geometry("800x600")
        self.transient(master)
        self.focus_force()

        self.canvas = ctk.CTkCanvas(self, width=800, height=600, bg="#2B2B2B", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.original_image = image
        self.scale = 1.0
        self.position = [400, 300]
        self.photo_image = None

        self._drag_data = {"x": 0, "y": 0}

        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move)

        self.update_idletasks()  # Garante que o canvas tenha as dimensões corretas
        self._set_initial_zoom()
        self.redraw()

    def _set_initial_zoom(self):
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        img_w, img_h = self.original_image.size

        fit_scale = min(canvas_w / img_w, canvas_h / img_h)

        self.scale = min(fit_scale * 1.35, 4.0)

        self.position = [canvas_w / 2, canvas_h / 2]

    def redraw(self):
        if self.scale < 0.1: self.scale = 0.1
        width = int(self.original_image.width * self.scale)
        height = int(self.original_image.height * self.scale)
        resized_image = self.original_image.resize((width, height), Image.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(resized_image)
        self.canvas.delete("all")
        self.canvas.create_image(self.position[0], self.position[1], anchor="center", image=self.photo_image)

    def on_mouse_wheel(self, event):
        scale_factor = 1.1 if event.delta > 0 else 0.9
        mouse_x, mouse_y = event.x, event.y
        img_coord_x = mouse_x - self.position[0]
        img_coord_y = mouse_y - self.position[1]
        self.position[0] -= img_coord_x * (scale_factor - 1)
        self.position[1] -= img_coord_y * (scale_factor - 1)
        self.scale *= scale_factor
        self.redraw()

    def on_button_press(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def on_move(self, event):
        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]
        self.position[0] += dx
        self.position[1] += dy
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y
        self.redraw()

if __name__ == "__main__":
    gate_or_exit()
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
