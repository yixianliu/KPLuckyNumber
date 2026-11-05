import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, messagebox
import logging

logging.basicConfig(level=logging.DEBUG)

try:
    root = tk.Tk()
    root.title('Test')
    root.geometry('800x600')
    
    # Try building a simple frame like the GUI does
    frame = tk.Frame(root, bg='white')
    frame.pack(fill='both', expand=True)
    
    lbl = tk.Label(frame, text='Hello World', bg='white', fg='black')
    lbl.pack(padx=20, pady=20)
    
    root.mainloop()
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()