import customtkinter as ctk
from tkinter import messagebox, simpledialog
import os
import pandas as pd
from PIL import Image, ImageTk, ImageDraw
import gspread
from google.oauth2.service_account import Credentials
import io
import socket
import datetime
import traceback
import threading

# --- CONFIGURAÇÕES GLOBAIS ---

# ALTERAÇÃO: Adicionada a variável para o caminho da pasta de imagens.
# Você DEVE alterar esta linha para o caminho correto onde as imagens estão.
# Exemplo para pasta local: IMAGE_FOLDER_PATH = r"C:\Users\SeuUsuario\Desktop\ImagensDeAvaliacao"
# Exemplo para pasta de rede: IMAGE_FOLDER_PATH = r"\\servidor\Compartilhamento\ImagensDeAvaliacao"
IMAGE_FOLDER_PATH = r".\img_dedos_teste"

SPREADSHEET_ID = "1D-jVs643kqIeGnHhy6Q2Dh4zcZfOn3jLsgv9AujNrII"

# ALTERAÇÃO: Removido o escopo do Google Drive da lista de permissões.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets"
]
SERVICE_ACCOUNT_FILE = 'credentials.json'
try:
    OPERATOR_ID = socket.gethostname()
except:
    OPERATOR_ID = "unknown_operator"

# --- LÓGICA DE NEGÓCIO E DADOS (GOOGLE APIS) ---


class TaskManager:
    # ALTERAÇÃO: O construtor __init__ foi simplificado. A inicialização do self.drive_service foi removida.
    def __init__(self):
        try:
            creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
            self.sheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID).sheet1
            self.df_columns = self.sheet.get_all_records()[0].keys()
        except FileNotFoundError:
            messagebox.showerror("Erro Crítico", f"Arquivo de credenciais '{SERVICE_ACCOUNT_FILE}' não encontrado.")
            raise
        except Exception as e:
            messagebox.showerror("Erro de Autenticação", f"Não foi possível conectar aos serviços do Google: {e}")
            raise

    def get_all_records_as_df(self):
        all_records = self.sheet.get_all_records()
        return pd.DataFrame(all_records, dtype=str)

    def fetch_and_reserve_batch(self, batch_size: int):
        df = self.get_all_records_as_df()
        if df.empty:
            return None, "Planilha vazia ou em formato incorreto."

        pending_df = df[df['status'] == 'pendente'].copy()
        if pending_df.empty:
            return None, "Todas as avaliações foram concluídas!"

        # Identificar mãos únicas pendentes
        unique_hands = []
        seen_person_hand = set()

        for index, row in pending_df.iterrows():
            try:
                parts = os.path.splitext(row['nome_imagem_digital'])[0].split('_')
                person_id = parts[0]
                finger_str = parts[1]
                finger_num = int(''.join(filter(str.isdigit, finger_str)))
                hand_name = "hand1" if 1 <= finger_num <= 5 else "hand2"
                person_hand_id = f"{person_id}_{hand_name}"

                if person_hand_id not in seen_person_hand:
                    seen_person_hand.add(person_hand_id)
                    
                    regex_pattern = f"^{person_id}_dedo[1-5](\.|$)"
                    if hand_name == "hand2":
                        regex_pattern = f"^{person_id}_dedo(?:6|7|8|9|10)(\.|$)"
                    
                    hand_df = df[df['nome_imagem_digital'].str.contains(regex_pattern, regex=True)].copy()
                    
                    pending_hand_df = hand_df[hand_df['status'] == 'pendente']

                    if not pending_hand_df.empty:
                        unique_hands.append({
                            "id": person_hand_id,
                            "image_name": f"column_{person_hand_id}.png",
                            "tasks": pending_hand_df
                        })
                if len(unique_hands) >= batch_size:
                    break
            except (IndexError, ValueError):
                continue 

        if not unique_hands:
            return None, "Não foi possível encontrar tarefas pendentes válidas."

        # Reservar tarefas na planilha
        cells_to_update = []
        col_status_idx = list(self.df_columns).index('status') + 1
        col_op_idx = list(self.df_columns).index('operador_id') + 1

        for hand in unique_hands:
            for index in hand["tasks"].index:
                row_num = index + 2
                cells_to_update.append(gspread.Cell(row_num, col_status_idx, 'em_progresso'))
                cells_to_update.append(gspread.Cell(row_num, col_op_idx, OPERATOR_ID))
        
        if cells_to_update:
            self.sheet.update_cells(cells_to_update, value_input_option='USER_ENTERED')
        
        return unique_hands, None

    # ALTERAÇÃO: A função process_image_batch foi completamente reescrita.
    def process_image_batch(self, hands_to_process, progress_callback):
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

    def update_batch_results(self, evaluated_tasks, unevaluated_tasks):
        cells_to_update = []
        timestamp = datetime.datetime.now().isoformat()

        col_f1_idx = list(self.df_columns).index('fator_1_recorte_correto') + 1
        col_f2_idx = list(self.df_columns).index('fator_2_qualidade_suficiente') + 1
        col_status_idx = list(self.df_columns).index('status') + 1
        col_time_idx = list(self.df_columns).index('timestamp_avaliacao') + 1
        col_op_idx = list(self.df_columns).index('operador_id') + 1

        # Tarefas concluídas
        for index, row_data in evaluated_tasks.iterrows():
            row_num = int(index) + 2
            cells_to_update.append(gspread.Cell(row_num, col_f1_idx, row_data['fator_1_recorte_correto']))
            cells_to_update.append(gspread.Cell(row_num, col_f2_idx, row_data['fator_2_qualidade_suficiente']))
            cells_to_update.append(gspread.Cell(row_num, col_status_idx, 'concluido'))
            cells_to_update.append(gspread.Cell(row_num, col_time_idx, timestamp))

        # Tarefas não concluídas (liberar)
        for index, row_data in unevaluated_tasks.iterrows():
            row_num = int(index) + 2
            cells_to_update.append(gspread.Cell(row_num, col_status_idx, 'pendente'))
            cells_to_update.append(gspread.Cell(row_num, col_op_idx, ''))

        if cells_to_update:
            self.sheet.update_cells(cells_to_update, value_input_option='USER_ENTERED')
        return True

# --- INTERFACE GRÁFICA ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MIcut Avaliator - Centralizado")
        self.geometry("500x300")
        self.resizable(False, False)
        self.task_manager = None
        self.current_screen = None
        
        try:
            self.task_manager = TaskManager()
            self.show_batch_selection_screen()
        except Exception as e:
            self.show_error_screen(f"Falha na inicialização: {e}")

    def show_screen(self, screen_class, *args, **kwargs):
        if self.current_screen:
            self.current_screen.destroy()
        
        geometry = kwargs.pop("geometry", "500x300")
        resizable = kwargs.pop("resizable", False)
        self.geometry(geometry)
        self.resizable(resizable, resizable)

        self.current_screen = screen_class(self, *args, **kwargs)
        self.current_screen.pack(expand=True, fill="both")

    def show_batch_selection_screen(self):
        self.show_screen(BatchSelectionScreen, geometry="500x300", resizable=False)

    def show_loading_screen(self, batch_size):
        self.show_screen(LoadingScreen, batch_size=batch_size, geometry="500x200", resizable=False)

    def show_evaluation_screen(self, batch_data, processed_image):
        self.show_screen(EvaluationScreen, batch_data=batch_data, processed_image=processed_image, geometry="1200x800", resizable=True)
        # Configura o comportamento de fechamento APÓS a tela de avaliação ter sido criada
        self.protocol("WM_DELETE_WINDOW", self.current_screen.on_closing)

    def show_error_screen(self, message):
        self.show_screen(ErrorScreen, message=message)

class BatchSelectionScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master)
        self.master = master

        ctk.CTkLabel(self, text="MIcut Avaliator", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(20,10))
        ctk.CTkLabel(self, text=f"Operador: {OPERATOR_ID}").pack(pady=5)
        
        ctk.CTkLabel(self, text="Quantas mãos você vai avaliar nesta sessão?").pack(pady=(20,5))
        self.batch_size_entry = ctk.CTkEntry(self, justify="center")
        self.batch_size_entry.pack(pady=5)
        self.batch_size_entry.insert(0, "10")

        ctk.CTkButton(self, text="Iniciar Avaliação", command=self.start_evaluation).pack(pady=20, ipady=10)

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
        # Executar em uma thread para não bloquear a UI
        threading.Thread(target=self._load_data_thread).start()

    def _load_data_thread(self):
        batch_data, error = self.master.task_manager.fetch_and_reserve_batch(self.batch_size)
        if error:
            messagebox.showerror("Erro", error)
            self.master.show_batch_selection_screen()
            return

        def progress_callback(message):
            self.progress_label.configure(text=message)

        processed_image = self.master.task_manager.process_image_batch(batch_data, progress_callback)
        
        # Voltar para a thread principal para atualizar a UI de forma segura
        self.master.after(0, self.on_loading_complete, batch_data, processed_image)

    def on_loading_complete(self, batch_data, processed_image):
        self.master.show_evaluation_screen(batch_data, processed_image)

class EvaluationScreen(ctk.CTkFrame):
    def __init__(self, master, batch_data, processed_image):
        super().__init__(master)
        self.master = master
        self.task_manager = master.task_manager
        self.batch_data = batch_data
        self.processed_image = processed_image
        
        self.current_task_index = 0
        self.results = pd.DataFrame() # Armazena resultados localmente

        self.setup_ui()
        self.load_current_task()

    def setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Painel de Ações (Esquerda) ---
        actions_frame = ctk.CTkFrame(self, width=200)
        actions_frame.grid(row=0, column=0, sticky="nswe", padx=10, pady=10)
        actions_frame.grid_propagate(False)
        
        self.progress_label = ctk.CTkLabel(actions_frame, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.progress_label.pack(pady=10, padx=10)

        self.next_button = ctk.CTkButton(actions_frame, text="Próxima Mão >>", command=self.next_task)
        self.next_button.pack(side="bottom", pady=10, padx=20, fill="x", ipady=10)
        
        self.save_exit_button = ctk.CTkButton(actions_frame, text="Salvar e Sair", command=self.on_closing)
        self.save_exit_button.pack(side="bottom", pady=10, padx=20, fill="x")

        # --- Painel da Coluna (Centro) ---
        col_frame = ctk.CTkFrame(self)
        col_frame.grid(row=0, column=1, sticky="nswe", padx=(0, 10), pady=10)
        self.col_image_label = ctk.CTkLabel(col_frame, text="")
        self.col_image_label.pack(expand=True, fill="both", padx=5, pady=5)

        # --- Painel de Recortes (Direita) ---
        self.crops_frame = ctk.CTkScrollableFrame(self, label_text="Recortes para Avaliação")
        self.crops_frame.grid(row=0, column=2, sticky="nswe", padx=(0, 10), pady=10)

    def load_current_task(self):
        # Limpar widgets antigos
        for widget in self.crops_frame.winfo_children():
            widget.destroy()

        current_hand = self.batch_data[self.current_task_index]
        self.current_task_df = current_hand["tasks"].copy()
        
        self.progress_label.configure(text=f"Avaliando Mão {self.current_task_index + 1} de {len(self.batch_data)}")
        if self.current_task_index == len(self.batch_data) - 1:
            self.next_button.configure(text="Finalizar e Salvar Tudo", fg_color="green")

        original_img = self.processed_image.get(current_hand["id"])
        if original_img is None:
            self.col_image_label.configure(text=f"Imagem para\n{current_hand['id']}\nnão foi baixada.", image=None)
            return

        display_img = original_img.copy()
        draw = ImageDraw.Draw(display_img, "RGBA")
        
        for index, row in self.current_task_df.iterrows():
            box = (int(row['bbox_x1']), int(row['bbox_y1']), int(row['bbox_x2']), int(row['bbox_y2']))
            draw.rectangle(box, outline="red", width=3)
            crop_image_obj = original_img.crop(box)
            self.create_crop_widget(index, row, crop_image_obj)

        w, h = display_img.size
        max_h = 800
        if h > max_h:
            ratio = max_h / h
            new_w = int(w * ratio)
            display_img = display_img.resize((new_w, max_h), Image.LANCZOS)

        ctk_img = ctk.CTkImage(light_image=display_img, size=display_img.size)
        self.col_image_label.configure(image=ctk_img)

    def create_crop_widget(self, df_index, data_row, crop_image: Image.Image):
        widget_frame = ctk.CTkFrame(self.crops_frame)
        widget_frame.pack(fill="x", pady=5, padx=5)
        widget_frame.grid_columnconfigure(1, weight=1)

        ctk_crop_img = ctk.CTkImage(light_image=crop_image, size=(100, 100))
        img_label = ctk.CTkLabel(widget_frame, image=ctk_crop_img, text="")
        img_label.bind("<Button-1>", lambda e, img=crop_image: ZoomWindow(self, img))
        img_label.grid(row=0, column=0, rowspan=3, padx=5, pady=5)

        ctk.CTkLabel(widget_frame, text=data_row['nome_imagem_digital'], font=ctk.CTkFont(weight="bold")).grid(row=0, column=1, sticky="w", padx=5)

        f1_var = ctk.StringVar(value=data_row.get('fator_1_recorte_correto', ''))
        f2_var = ctk.StringVar(value=data_row.get('fator_2_qualidade_suficiente', ''))

        # Fator 1
        f1_frame = ctk.CTkFrame(widget_frame, fg_color="transparent")
        f1_frame.grid(row=1, column=1, sticky="ew", padx=5)
        ctk.CTkLabel(f1_frame, text="Recorte correto?").pack(side="left")
        ctk.CTkRadioButton(f1_frame, text="Não", variable=f1_var, value="Não").pack(side="right")
        ctk.CTkRadioButton(f1_frame, text="Sim", variable=f1_var, value="Sim").pack(side="right", padx=5)
        
        # Fator 2
        f2_frame = ctk.CTkFrame(widget_frame, fg_color="transparent")
        f2_frame.grid(row=2, column=1, sticky="ew", padx=5)
        ctk.CTkLabel(f2_frame, text="Qualidade suficiente?").pack(side="left")
        ctk.CTkRadioButton(f2_frame, text="Não", variable=f2_var, value="Não").pack(side="right")
        ctk.CTkRadioButton(f2_frame, text="Sim", variable=f2_var, value="Sim").pack(side="right", padx=5)

        self.current_task_df.loc[df_index, 'f1_var'] = f1_var
        self.current_task_df.loc[df_index, 'f2_var'] = f2_var

    def next_task(self):
        # Validar se a tarefa atual foi concluída
        for index, row in self.current_task_df.iterrows():
            if not row['f1_var'].get() or not row['f2_var'].get():
                messagebox.showwarning("Atenção", "Por favor, avalie todos os recortes antes de avançar.")
                return
        
        # Salvar resultados da tarefa atual em memória
        for index, row in self.current_task_df.iterrows():
            self.current_task_df.loc[index, 'fator_1_recorte_correto'] = row['f1_var'].get()
            self.current_task_df.loc[index, 'fator_2_qualidade_suficiente'] = row['f2_var'].get()
        
        self.results = pd.concat([self.results, self.current_task_df.drop(columns=['f1_var', 'f2_var'])])

        if self.current_task_index < len(self.batch_data) - 1:
            self.current_task_index += 1
            self.load_current_task()
        else:
            # Chegou ao fim do lote
            self.on_closing(final_save=True)

    def on_closing(self, final_save=False):
        if not final_save:
            if not messagebox.askyesno("Confirmar Saída", "Tem certeza que deseja sair? Seu progresso será salvo e as tarefas não concluídas serão liberadas."):
                return

        # Identificar tarefas não avaliadas no lote
        evaluated_indices = self.results.index
        all_indices = pd.concat([hand["tasks"] for hand in self.batch_data]).index
        unevaluated_indices = all_indices.difference(evaluated_indices)
        
        all_tasks_df = pd.concat([hand["tasks"] for hand in self.batch_data])
        unevaluated_df = all_tasks_df.loc[unevaluated_indices]

        self.task_manager.update_batch_results(self.results, unevaluated_df)
        
        if final_save:
            messagebox.showinfo("Sucesso", "Lote de avaliação concluído e salvo com sucesso!")
        else:
            messagebox.showinfo("Progresso Salvo", "Seu progresso foi salvo. As tarefas restantes foram liberadas.")

        self.master.protocol("WM_DELETE_WINDOW", self.master.destroy)
        self.master.show_batch_selection_screen()

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

        self.redraw()

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
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()