

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
import ctypes
from ctypes import wintypes

# --- Bloco de Inicialização do Oracle Client ---
try:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        lib_dir = os.path.join(sys._MEIPASS, "instantclient_19_27")
        oracledb.init_oracle_client(lib_dir=lib_dir)
    else:
        oracledb.init_oracle_client()
except oracledb.DatabaseError as e:
     messagebox.showerror("Erro Crítico de Banco de Dados", 
                          f"Não foi possível inicializar o Oracle Client. Verifique a instalação.\n\nDetalhe: {e}")
     sys.exit(1)

# --- CONFIGURAÇÕES GLOBAIS ---

IMAGE_FOLDER_PATH = r".\Fingerprints_Colums"
ORACLE_DSN = "host:porta/service_name"  # Ex: "detran" ou "192.168.1.50:1521/ORCL"

# Nomes das tabelas
TABLE_RECORTE = "FRC.RECORTE"
TABLE_RECORTE_ANALISE = "FRC.RECORTE_ANALISE"
TABLE_RECORTE_STATUS = "FRC.RECORTE_STATUS"
TABLE_OPERADORES = "Detran_Operadores" # Tabela de operadores, mudem o nome pro real

# Mapeamento dos códigos de status
STATUS_PENDENTE = 10
STATUS_EM_PROCESSAMENTO = 20
STATUS_CONCLUIDO = 40

# --- LÓGICA DE NEGÓCIO E DADOS (ORACLE DB) ---

class TaskManager:
    def __init__(self, user, password, dsn):
        self.conn = None
        try:
            self.conn = oracledb.connect(user=user, password=password, dsn=dsn)
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
        if self.conn:
            self.conn.close()

    def fetch_and_reserve_batch(self, batch_size: int):
        try:
            with self.conn.cursor() as cursor:
                self.conn.autocommit = False
                sql_fetch_pending = f"SELECT NU_PID, CO_DEDO, FILENAME, BOX_X, BOX_Y, BOX_W, BOX_H FROM {TABLE_RECORTE} WHERE TP_RECORTE_STATUS = :status_pendente"
                cursor.execute(sql_fetch_pending, status_pendente=STATUS_PENDENTE)
                db_columns = [desc[0].lower() for desc in cursor.description]
                pending_df = pd.DataFrame(cursor.fetchall(), columns=db_columns)
                if pending_df.empty:
                    return None, "Todas as avaliações foram concluídas!"
                rename_map = {
                    'nu_pid': 'nu_pid', 'co_dedo': 'co_dedo', 'filename': 'nome_imagem_digital',
                    'box_x': 'bbox_x1', 'box_y': 'bbox_y1', 'box_w': 'box_w', 'box_h': 'box_h'
                }
                pending_df.rename(columns=rename_map, inplace=True)
                pending_df['bbox_x2'] = pending_df['bbox_x1'] + pending_df['box_w']
                pending_df['bbox_y2'] = pending_df['bbox_y1'] + pending_df['box_h']
                pending_df.set_index(['nu_pid', 'co_dedo'], inplace=True)
                def get_person_hand_id(image_name):
                    try:
                        parts = os.path.splitext(str(image_name))[0].split('_')
                        person_id = parts[0]
                        finger_str = parts[1]
                        finger_num = int("".join(filter(str.isdigit, finger_str)))
                        hand_name = "hand1" if 1 <= finger_num <= 5 else "hand2"
                        return f"{person_id}_{hand_name}"
                    except (IndexError, ValueError): return None
                pending_df['person_hand_id'] = pending_df['nome_imagem_digital'].apply(get_person_hand_id)
                pending_df.dropna(subset=['person_hand_id'], inplace=True)
                unique_hand_ids = pending_df['person_hand_id'].unique()
                if len(unique_hand_ids) == 0: return None, "Não foi possível encontrar tarefas pendentes com formato de nome válido."
                batch_hand_ids = unique_hand_ids[:batch_size]
                tasks_to_reserve_df = pending_df[pending_df['person_hand_id'].isin(batch_hand_ids)].copy()
                task_ids_to_reserve = tasks_to_reserve_df.index.tolist()
                if not task_ids_to_reserve: return None, "Nenhuma tarefa encontrada para o lote selecionado."
                id_placeholders = ", ".join([f"(:pid{i}, :cid{i})" for i in range(len(task_ids_to_reserve))])
                lock_sql = f"SELECT NU_PID FROM {TABLE_RECORTE} WHERE (NU_PID, CO_DEDO) IN ({id_placeholders}) AND TP_RECORTE_STATUS = :status_pendente FOR UPDATE"
                bind_vars = {'status_pendente': STATUS_PENDENTE}
                for i, (pid, cid) in enumerate(task_ids_to_reserve): bind_vars[f'pid{i}'] = pid; bind_vars[f'cid{i}'] = cid
                cursor.execute(lock_sql, bind_vars)
                locked_rows = cursor.fetchall()
                if len(locked_rows) != len(task_ids_to_reserve):
                    self.conn.rollback()
                    return None, "Conflito de reserva. Outro operador pode ter pego estas tarefas. Tente novamente."
                update_sql = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :status_proc WHERE NU_PID = :pid AND CO_DEDO = :cid"
                update_data = [{'status_proc': STATUS_EM_PROCESSAMENTO, 'pid': pid, 'cid': cid} for pid, cid in task_ids_to_reserve]
                cursor.executemany(update_sql, update_data)
                self.conn.commit()
                unique_hands = []
                for hand_id in batch_hand_ids:
                    hand_tasks_df = tasks_to_reserve_df[tasks_to_reserve_df['person_hand_id'] == hand_id]
                    unique_hands.append({"id": hand_id, "image_name": f"column_{hand_id}.png", "tasks": hand_tasks_df})
                return unique_hands, None
        except oracledb.DatabaseError as e:
            if self.conn: self.conn.rollback()
            return None, f"Erro no banco de dados durante a busca: {e}"
        finally:
            if self.conn: self.conn.autocommit = True

    def process_image_batch(self, hands_to_process, progress_callback):
        processed_image = {}
        total_hands = len(hands_to_process)
        if not os.path.isdir(IMAGE_FOLDER_PATH): messagebox.showerror("Erro de Configuração", f"O caminho das imagens não foi encontrado:\n{IMAGE_FOLDER_PATH}"); return {}
        for i, hand in enumerate(hands_to_process):
            image_name = hand["image_name"]
            progress_callback(f"Carregando: {image_name} ({i+1}/{total_hands})")
            full_image_path = os.path.join(IMAGE_FOLDER_PATH, image_name)
            try:
                if not os.path.exists(full_image_path): raise FileNotFoundError(f"Imagem '{image_name}' não encontrada em:\n{full_image_path}")
                image = Image.open(full_image_path)
                processed_image[hand["id"]] = image
            except Exception as e: messagebox.showerror("Erro ao Carregar Imagem", f"Falha ao carregar {image_name}: {e}"); processed_image[hand["id"]] = None
        return processed_image

    def update_batch_results(self, evaluated_tasks, unevaluated_tasks, operator_id_to_save):
        try:
            with self.conn.cursor() as cursor:
                self.conn.autocommit = False
                timestamp = datetime.datetime.now()
                if not evaluated_tasks.empty:
                    sql_analise = f"INSERT INTO {TABLE_RECORTE_ANALISE} (NU_PID, CO_DEDO, FATOR_1_RECORTE_CORRETO, FATOR_2_QUALIDADE_SUFICIENTE, NU_RICOPER, DT_TRATPEDIDO) VALUES (:pid, :cid, :f1, :f2, :op_id, :ts)"
                    eval_data_insert = [
                        {'pid': int(index[0]), 'cid': int(index[1]),
                         'f1': 1 if row['fator_1_recorte_correto'] == 'SIM' else 0,
                         'f2': 1 if row['fator_2_qualidade_suficiente'] == 'SIM' else 0,
                         'op_id': operator_id_to_save, 'ts': timestamp}
                        for index, row in evaluated_tasks.iterrows()]
                    cursor.executemany(sql_analise, eval_data_insert)
                    update_sql_recorte = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :status WHERE NU_PID = :pid AND CO_DEDO = :cid"
                    eval_data_update = [{'status': STATUS_CONCLUIDO, 'pid': int(index[0]), 'cid': int(index[1])} for index, row in evaluated_tasks.iterrows()]
                    cursor.executemany(update_sql_recorte, eval_data_update)
                if not unevaluated_tasks.empty:
                    update_uneval_sql = f"UPDATE {TABLE_RECORTE} SET TP_RECORTE_STATUS = :status WHERE NU_PID = :pid AND CO_DEDO = :cid"
                    uneval_data = [{'status': STATUS_PENDENTE, 'pid': int(index[0]), 'cid': int(index[1])} for index, row in unevaluated_tasks.iterrows()]
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

class LoginScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        self.master = master
        ctk.CTkLabel(self, text="Login", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(20,10))
        ctk.CTkLabel(self, text="Usuário").pack(pady=(20,5))
        self.user_entry = ctk.CTkEntry(self, justify="center")
        self.user_entry.pack(pady=5)
        ctk.CTkLabel(self, text="Senha").pack(pady=(10,5))
        self.password_entry = ctk.CTkEntry(self, justify="center", show="*")
        self.password_entry.pack(pady=5)
        ctk.CTkButton(self, text="Login", command=self.login).pack(pady=20, ipady=10)

    def login(self):
        username = self.user_entry.get()
        password = self.password_entry.get()
        if not username or not password: messagebox.showerror("Erro de Login", "Usuário e senha não podem estar em branco."); return
        try:
            dll_path = os.path.join("bin", "Senha.dll")
            if not os.path.exists(dll_path): messagebox.showerror("Erro Crítico", f"Não foi possível encontrar Senha.dll no diretório 'bin'."); return
            senha_dll = ctypes.CDLL(dll_path)
            CalculaSenhaBD = senha_dll.CalculaSenhaBD
            CalculaSenhaBD.argtypes = [wintypes.LPCSTR, wintypes.LPSTR]
            CalculaSenhaBD.restype = None
            output_buffer = ctypes.create_string_buffer(256)
            CalculaSenhaBD(password.encode('ascii'), output_buffer)
            encrypted_password = output_buffer.value.decode('ascii')
            self.master.attempt_login(username, encrypted_password)
        except Exception as e: messagebox.showerror("Erro de Criptografia", f"Ocorreu um erro ao chamar a função da DLL: {e}")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MIcut Avaliator - Centralizado")
        self.geometry("500x350"); self.resizable(False, False)
        self.task_manager = None
        self.current_screen = None
        self.login_username = None
        self.operator_full_name = None
        self.show_login_screen()

    def show_screen(self, screen_class, *args, **kwargs):
        if self.current_screen: self.current_screen.destroy()
        geometry = kwargs.pop("geometry", "500x350"); resizable = kwargs.pop("resizable", False)
        self.geometry(geometry); self.resizable(resizable, resizable)
        self.current_screen = screen_class(self, *args, **kwargs)
        self.current_screen.pack(expand=True, fill="both")

    def show_login_screen(self):
        self.show_screen(LoginScreen, geometry="500x350", resizable=False)

    def attempt_login(self, username, password):
        try:
            self.task_manager = TaskManager(user=username, password=password, dsn=ORACLE_DSN)
            self.login_username = username
            # Busca o nome completo do operador
            try:
                with self.task_manager.conn.cursor() as cursor:
                    # Aoooo johnson, Mude pro nome real das colunas :)
                    #Chemei de ID a coluna do numero e nome a coluna dos nomes.
                    sql = f"SELECT nome FROM {TABLE_OPERADORES} WHERE ID = :user_id"
                    cursor.execute(sql, user_id=username)
                    result = cursor.fetchone()
                    if result:
                        self.operator_full_name = result[0]
                    else:
                        self.operator_full_name = username # Fallback para o username se não encontrar
            except Exception as e:
                print(f"Aviso: Não foi possível buscar o nome do operador. Usando o ID. Erro: {e}")
                self.operator_full_name = username # Fallback em caso de erro
            self.show_batch_selection_screen()
        except Exception: pass

    def show_batch_selection_screen(self):
        self.show_screen(BatchSelectionScreen, operator_name=self.operator_full_name, geometry="500x300", resizable=False)

    def show_loading_screen(self, batch_size):
        self.show_screen(LoadingScreen, batch_size=batch_size, geometry="500x200", resizable=False)

    def show_evaluation_screen(self, batch_data, processed_image):
        self.show_screen(EvaluationScreen, batch_data=batch_data, processed_image=processed_image, login_username_to_save=self.login_username, geometry="1200x800", resizable=True)
        self.protocol("WM_DELETE_WINDOW", self.current_screen.on_closing)

    def show_error_screen(self, message):
        self.show_screen(ErrorScreen, message=message)

class BatchSelectionScreen(ctk.CTkFrame):
    def __init__(self, master, operator_name):
        super().__init__(master)
        self.master = master
        ctk.CTkLabel(self, text="MIcut Avaliator", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(20,10))
        ctk.CTkLabel(self, text=f"Operador: {operator_name}").pack(pady=5)
        ctk.CTkLabel(self, text="Quantas mãos você vai avaliar nesta sessão?").pack(pady=(20,5))
        self.batch_size_entry = ctk.CTkEntry(self, justify="center"); self.batch_size_entry.pack(pady=5); self.batch_size_entry.insert(0, "10")
        ctk.CTkButton(self, text="Iniciar Avaliação", command=self.start_evaluation).pack(pady=20, ipady=10)

    def start_evaluation(self):
        try:
            batch_size = int(self.batch_size_entry.get())
            if batch_size <= 0: raise ValueError
            self.master.show_loading_screen(batch_size)
        except (ValueError, TypeError): messagebox.showerror("Entrada Inválida", "Por favor, insira um número inteiro positivo.")

class LoadingScreen(ctk.CTkFrame):
    def __init__(self, master, batch_size):
        super().__init__(master)
        self.master = master; self.batch_size = batch_size
        self.progress_label = ctk.CTkLabel(self, text="Buscando e reservando tarefas...", font=ctk.CTkFont(size=14)); self.progress_label.pack(pady=(40, 10))
        self.progress_bar = ctk.CTkProgressBar(self, mode='indeterminate'); self.progress_bar.pack(pady=10, padx=20, fill="x"); self.progress_bar.start()
        self.after(100, self.load_data)

    def load_data(self):
        threading.Thread(target=self._load_data_thread).start()

    def _load_data_thread(self):
        batch_data, error = self.master.task_manager.fetch_and_reserve_batch(self.batch_size)
        if error: self.master.after(0, lambda: messagebox.showerror("Erro", error)); self.master.after(0, self.master.show_batch_selection_screen); return
        def progress_callback(message): self.master.after(0, self.progress_label.configure, {"text": message})
        processed_image = self.master.task_manager.process_image_batch(batch_data, progress_callback)
        self.master.after(0, self.on_loading_complete, batch_data, processed_image)

    def on_loading_complete(self, batch_data, processed_image):
        self.master.show_evaluation_screen(batch_data, processed_image)

class EvaluationScreen(ctk.CTkFrame):
    def __init__(self, master, batch_data, processed_image, login_username_to_save):
        super().__init__(master)
        self.master = master
        self.task_manager = master.task_manager
        self.batch_data = batch_data
        self.processed_image = processed_image
        self.login_username_to_save = login_username_to_save
        self.current_task_index = 0
        self.results = pd.DataFrame()
        self.setup_ui()
        self.load_current_task()

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=1); self.grid_columnconfigure(2, weight=1); self.grid_rowconfigure(0, weight=1)
        actions_frame = ctk.CTkFrame(self, width=200); actions_frame.grid(row=0, column=0, sticky="nswe", padx=10, pady=10); actions_frame.grid_propagate(False)
        self.progress_label = ctk.CTkLabel(actions_frame, text="", font=ctk.CTkFont(size=14, weight="bold")); self.progress_label.pack(pady=10, padx=10)
        self.next_button = ctk.CTkButton(actions_frame, text="Próxima Mão >>", command=self.next_task); self.next_button.pack(side="bottom", pady=10, padx=20, fill="x", ipady=10)
        self.save_exit_button = ctk.CTkButton(actions_frame, text="Salvar e Sair", command=self.on_closing); self.save_exit_button.pack(side="bottom", pady=10, padx=20, fill="x")
        col_frame = ctk.CTkFrame(self); col_frame.grid(row=0, column=1, sticky="nswe", padx=(0, 10), pady=10)
        self.col_image_label = ctk.CTkLabel(col_frame, text=""); self.col_image_label.pack(expand=True, fill="both", padx=5, pady=5)
        self.crops_frame = ctk.CTkScrollableFrame(self, label_text="Recortes para Avaliação"); self.crops_frame.grid(row=0, column=2, sticky="nswe", padx=(0, 10), pady=10)

    def load_current_task(self):
        for widget in self.crops_frame.winfo_children(): widget.destroy()
        current_hand = self.batch_data[self.current_task_index]
        self.current_task_df = current_hand["tasks"].copy()
        self.progress_label.configure(text=f"Avaliando Mão {self.current_task_index + 1} de {len(self.batch_data)}")
        if self.current_task_index == len(self.batch_data) - 1: self.next_button.configure(text="Finalizar e Salvar Tudo", fg_color="green")
        original_img = self.processed_image.get(current_hand["id"])
        if original_img is None: self.col_image_label.configure(text=f"Imagem para\n{current_hand['id']}\nnão foi carregada.", image=None); return
        display_img = original_img.copy(); draw = ImageDraw.Draw(display_img, "RGBA")
        for df_index, row in self.current_task_df.iterrows():
            box = (int(row['bbox_x1']), int(row['bbox_y1']), int(row['bbox_x2']), int(row['bbox_y2']))
            draw.rectangle(box, outline="red", width=3)
            crop_image_obj = original_img.crop(box)
            self.create_crop_widget(df_index, row, crop_image_obj)
        max_w, max_h = 550, 750; original_w, original_h = display_img.size
        if original_w > max_w or original_h > max_h:
            ratio = min(max_w / original_w, max_h / original_h)
            new_w, new_h = int(original_w * ratio), int(original_h * ratio)
            display_img = display_img.resize((new_w, new_h), Image.LANCZOS)
        ctk_img = ctk.CTkImage(light_image=display_img, size=display_img.size)
        self.col_image_label.configure(image=ctk_img, text="")

    def create_crop_widget(self, df_index, data_row, crop_image: Image.Image):
        widget_frame = ctk.CTkFrame(self.crops_frame); widget_frame.pack(fill="x", pady=5, padx=5); widget_frame.grid_columnconfigure(1, weight=1)
        original_w, original_h = crop_image.size; ratio = min(100 / original_w, 100 / original_h); new_w, new_h = int(original_w * ratio), int(original_h * ratio)
        ctk_crop_img = ctk.CTkImage(light_image=crop_image, size=(new_w, new_h))
        img_label = ctk.CTkLabel(widget_frame, image=ctk_crop_img, text=""); img_label.bind("<Button-1>", lambda e, img=crop_image: ZoomWindow(self, img)); img_label.grid(row=0, column=0, rowspan=3, padx=5, pady=5)
        ctk.CTkLabel(widget_frame, text=data_row['nome_imagem_digital'], font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, sticky="w", padx=5)
        f1_var = ctk.StringVar(value=str(data_row.get('fator_1_recorte_correto', ''))); f2_var = ctk.StringVar(value=str(data_row.get('fator_2_qualidade_suficiente', '')))
        f1_frame = ctk.CTkFrame(widget_frame, fg_color="transparent"); f1_frame.grid(row=1, column=1, sticky="ew", padx=5)
        ctk.CTkLabel(f1_frame, text="Recorte correto?").pack(side="left"); ctk.CTkRadioButton(f1_frame, text="Não", variable=f1_var, value="NAO").pack(side="right"); ctk.CTkRadioButton(f1_frame, text="Sim", variable=f1_var, value="SIM").pack(side="right", padx=5)
        f2_frame = ctk.CTkFrame(widget_frame, fg_color="transparent"); f2_frame.grid(row=2, column=1, sticky="ew", padx=5)
        ctk.CTkLabel(f2_frame, text="Qualidade suficiente?").pack(side="left"); ctk.CTkRadioButton(f2_frame, text="Não", variable=f2_var, value="NAO").pack(side="right"); ctk.CTkRadioButton(f2_frame, text="Sim", variable=f2_var, value="SIM").pack(side="right", padx=5)
        self.current_task_df.loc[df_index, 'f1_var'] = f1_var; self.current_task_df.loc[df_index, 'f2_var'] = f2_var

    def next_task(self):
        for index, row in self.current_task_df.iterrows():
            if not row['f1_var'].get() or not row['f2_var'].get(): messagebox.showwarning("Atenção", "Por favor, avalie todos os recortes antes de avançar."); return
        for index, row in self.current_task_df.iterrows():
            self.current_task_df.loc[index, 'fator_1_recorte_correto'] = row['f1_var'].get()
            self.current_task_df.loc[index, 'fator_2_qualidade_suficiente'] = row['f2_var'].get()
        self.results = pd.concat([self.results, self.current_task_df.drop(columns=['f1_var', 'f2_var'])])
        if self.current_task_index < len(self.batch_data) - 1: self.current_task_index += 1; self.load_current_task()
        else: self.on_closing(final_save=True)

    def on_closing(self, final_save=False):
        if not final_save: 
            if not messagebox.askyesno("Confirmar Saída", "Tem certeza que deseja sair? Apenas as MÃOS 100% concluídas serão salvas."): return
        all_tasks_df = pd.concat([hand["tasks"] for hand in self.batch_data])
        completed_hand_ids = []
        if not self.results.empty:
            evaluated_fingers_by_hand = self.results.groupby('person_hand_id')
            for hand_data in self.batch_data:
                hand_id = hand_data["id"]
                total_fingers_in_hand = len(hand_data["tasks"])
                if hand_id in evaluated_fingers_by_hand.groups:
                    evaluated_count = len(evaluated_fingers_by_hand.get_group(hand_id))
                    if evaluated_count == total_fingers_in_hand: completed_hand_ids.append(hand_id)
        tasks_to_save_df = all_tasks_df[all_tasks_df['person_hand_id'].isin(completed_hand_ids)]
        tasks_to_release_df = all_tasks_df[~all_tasks_df['person_hand_id'].isin(completed_hand_ids)]
        if not tasks_to_save_df.empty:
            results_to_merge = self.results.reset_index().set_index(['nu_pid', 'co_dedo'])
            tasks_to_save_df = tasks_to_save_df.merge(results_to_merge[['fator_1_recorte_correto', 'fator_2_qualidade_suficiente']], left_index=True, right_index=True, how='left')
        self.task_manager.update_batch_results(tasks_to_save_df, tasks_to_release_df, operator_id_to_save=self.login_username_to_save)
        if final_save: messagebox.showinfo("Sucesso", "Lote de avaliação concluído e salvo com sucesso!")
        else: messagebox.showinfo("Progresso Salvo", "Seu progresso foi salvo. Tarefas de mãos incompletas foram liberadas.")
        self.master.protocol("WM_DELETE_WINDOW", self.master.destroy)
        self.master.show_batch_selection_screen()

class ErrorScreen(ctk.CTkFrame):
    def __init__(self, master, message): super().__init__(master); ctk.CTkLabel(self, text="Ocorreu um erro irrecuperável:", text_color="red").pack(pady=(20,5)); ctk.CTkLabel(self, text=message, wraplength=480).pack(pady=5, padx=10)

class ZoomWindow(ctk.CTkToplevel):
    def __init__(self, master, image: Image.Image):
        super().__init__(master); self.title("Zoom (Scroll para zoom, clique e arraste para mover)"); self.geometry("800x600"); self.transient(master); self.focus_force()
        self.canvas = ctk.CTkCanvas(self, width=800, height=600, bg="#2B2B2B", highlightthickness=0); self.canvas.pack(fill="both", expand=True)
        self.original_image = image; self.scale = 1.0; self.position = [400, 300]; self.photo_image = None; self._drag_data = {"x": 0, "y": 0}
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel); self.canvas.bind("<ButtonPress-1>", self.on_button_press); self.canvas.bind("<B1-Motion>", self.on_move); self.redraw()
    def redraw(self):
        if self.scale < 0.1: self.scale = 0.1
        width = int(self.original_image.width * self.scale); height = int(self.original_image.height * self.scale)
        resized_image = self.original_image.resize((width, height), Image.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(resized_image); self.canvas.delete("all"); self.canvas.create_image(self.position[0], self.position[1], anchor="center", image=self.photo_image)
    def on_mouse_wheel(self, event):
        scale_factor = 1.1 if event.delta > 0 else 0.9; mouse_x, mouse_y = event.x, event.y
        img_coord_x = mouse_x - self.position[0]; img_coord_y = mouse_y - self.position[1]
        self.position[0] -= img_coord_x * (scale_factor - 1); self.position[1] -= img_coord_y * (scale_factor - 1)
        self.scale *= scale_factor; self.redraw()
    def on_button_press(self, event): self._drag_data["x"] = event.x; self._drag_data["y"] = event.y
    def on_move(self, event):
        dx = event.x - self._drag_data["x"]; dy = event.y - self._drag_data["y"]
        self.position[0] += dx; self.position[1] += dy
        self._drag_data["x"] = event.x; self._drag_data["y"] = event.y; self.redraw()

if __name__ == "__main__":
    ctk.set_appearance_mode("System"); ctk.set_default_color_theme("blue"); app = App(); app.mainloop()
