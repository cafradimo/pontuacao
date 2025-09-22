import os
import re
import tempfile
import shutil
from io import BytesIO
import pandas as pd
import streamlit as st
import pdfplumber
from fpdf import FPDF
from datetime import datetime
from PIL import Image
import zipfile

# =================== CONFIGURAÇÃO ===================
st.set_page_config(page_title="CREA-RJ", layout="wide", page_icon="")

# =================== TABELA DE PONTUAÇÃO ===================
TABELA_PONTUACAO = {
    'SIM': {
        'RFs': 1,
        'Regularização': 5,
        'Ações': 1,
        'Ofícios': 1,
        'Resposta Ofícios': 2,
        'Protocolos': 1,
        'Fotos': 1
    },
    'NÃO': {
        'RFs': 0.5,
        'Regularização': 2.5,
        'Ações': 0.5,
        'Ofícios': 0.5,
        'Resposta Ofícios': 1,
        'Protocolos': 0.5,
        'Fotos': 0
    }
}

# =================== FUNÇÕES AUXILIARES ===================
def criar_temp_dir():
    """Cria diretório temporário"""
    return tempfile.mkdtemp()

def limpar_temp_dir(temp_dir):
    """Remove diretório temporário"""
    shutil.rmtree(temp_dir, ignore_errors=True)

def is_empty_info(text):
    """Verifica se o texto indica informação ausente"""
    if not text or str(text).strip() == '':
        return True
    return bool(re.search(r'^(SEM|NAO|NÃO|NAO INFORMADO|SEM INFORMAÇÃO)\s*[A-Z]*\s*$', str(text).strip(), re.IGNORECASE))

def clean_text(text):
    """Limpa texto removendo espaços extras e normalizando"""
    if not text:
        return ''
    text = str(text).replace('\n', ' ').strip()
    return ' '.join(text.split())

def formatar_agente_fiscalizacao(texto):
    """Formata o agente de fiscalização para manter apenas número e primeiro nome"""
    if not texto:
        return ''
    
    match = re.match(r'(\d+\s*-\s*)([A-Za-zÀ-ÿ\s]+)', texto)
    if match:
        numero = match.group(1).strip()
        nome_completo = match.group(2).strip()
        primeiro_nome = nome_completo.split()[0].capitalize()
        return f"{numero} {primeiro_nome}"
    return texto

def get_nome_completo_agente(texto):
    """Obtém o nome completo do agente de fiscalização"""
    if not texto:
        return ''
    
    match = re.match(r'\d+\s*-\s*([A-Za-zÀ-ÿ\s]+)', texto)
    if match:
        return match.group(1).strip()
    return texto

def formatar_responsavel(texto):
    """Formata o responsável para manter apenas a sigla inicial"""
    if not texto:
        return ''
    
    partes = [part.strip() for part in texto.split('-') if part.strip()]
    if partes:
        return partes[0]
    return texto

def formatar_data_relatorio(texto):
    """Extrai apenas a data do campo Data Relatório"""
    if not texto:
        return ''
    
    match = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    if match:
        return match.group(1)
    return ''

def extrair_numero_protocolo(texto):
    """Extrai apenas o número do protocolo do campo Fato Gerador"""
    if not texto:
        return ''
    
    match = re.search(r'(?:PROCESSO|PROTOCOLO)[/\s]*(\d+)', texto, re.IGNORECASE)
    if match:
        return match.group(1)
    return ''

def extrair_numero_autuacao(texto):
    """Extrai o número de autuação do texto da seção 04"""
    if not texto:
        return ''
    
    match = re.search(r'AUTUA[ÇC]AO\s+(\d+)', texto, re.IGNORECASE)
    if match:
        return match.group(1)
    return ''

def contar_ramos_atividade_secao_04(texto):
    """Conta a quantidade de vezes que 'Ramo Atividade :' aparece na seção 04"""
    if not texto or is_empty_info(texto):
        return 0
    
    padrao = r'04\s*-\s*Identificação dos Contratados, Responsáveis Técnicos e/ou Fiscalizados(.*?)(?=05\s*-\s*Documentos Solicitados|\Z)'
    match_secao = re.search(padrao, texto, re.DOTALL | re.IGNORECASE)
    
    if not match_secao:
        return 0
    
    conteudo_secao = match_secao.group(1)
    ocorrencias = re.findall(r'Ramo\s+Atividade\s*:', conteudo_secao, re.IGNORECASE)
    return len(ocorrencias)

def contar_autuacoes_secao_04(texto):
    """Conta a quantidade de vezes que a palavra AUTUACAO aparece na seção 04, item 'Motivo Ação'"""
    if not texto or is_empty_info(texto):
        return 0
    
    padrao = r'04\s*-\s*Identificação dos Contratados, Responsáveis Técnicos e/ou Fiscalizados(.*?)(?=05\s*-\s*Documentos Solicitados|\Z)'
    match_secao = re.search(padrao, texto, re.DOTALL | re.IGNORECASE)
    
    if not match_secao:
        return 0
    
    conteudo_secao = match_secao.group(1)
    padrao_motivo_acao = r'Motivo\s+A[çc][aã]o\s*:(.*?)(?=Ramo\s+Atividade|Documento|Responsável|$|\n\n)'
    matches_motivo = re.findall(padrao_motivo_acao, conteudo_secao, re.DOTALL | re.IGNORECASE)
    
    contador = 0
    for motivo in matches_motivo:
        ocorrencias = re.findall(r'AUTUA[ÇC]AO', motivo, re.IGNORECASE)
        contador += len(ocorrencias)
    
    return contador

def extrair_rf_principal(texto):
    """Extrai o RF Principal do texto"""
    if not texto:
        return ''
    
    match = re.search(r'RF Principal\s*:\s*(\d+)', texto, re.IGNORECASE)
    if match:
        return match.group(1)
    return ''

def extrair_secao(texto, titulo_secao):
    """Extrai o conteúdo de uma seção específica do PDF"""
    padrao = re.compile(
        r'{}(.*?)(?=\d{{2}}\s*-\s*[A-Z]|\Z)'.format(re.escape(titulo_secao)), 
        re.DOTALL | re.IGNORECASE
    )
    match = padrao.search(texto)
    if match:
        conteudo = match.group(1).strip()
        return None if is_empty_info(conteudo) else conteudo
    
    padrao_alternativo = re.compile(
        r'{}\s*(.*?)'.format(re.escape(titulo_secao)), 
        re.DOTALL | re.IGNORECASE
    )
    match_alt = padrao_alternativo.search(texto)
    if match_alt:
        conteudo = match_alt.group(1).strip()
        conteudo = re.split(r'\d{2}\s*-\s*[A-Z]', conteudo)[0].strip()
        return None if is_empty_info(conteudo) else conteudo
    
    return None

def verificar_oficio(texto):
    """Verifica se contém registros de ofício no texto"""
    if not texto or is_empty_info(texto):
        return 0
    
    padroes = [
        r'of[ií]cio',
        r'of\.',
        r'ofc',
        r'oficio',
        r'of[\s\-]?[0-9]'
    ]
    
    texto_str = str(texto).lower()
    for padrao in padroes:
        if re.search(padrao, texto_str, re.IGNORECASE):
            return 1
    return 0

def verificar_resposta_oficio(texto):
    """Verifica se contém 'Cópia ART' no texto"""
    if not texto or is_empty_info(texto):
        return 0
    
    texto_str = str(texto).lower()
    if re.search(r'c[óo]pia\s+art', texto_str, re.IGNORECASE):
        return 1
    return 0

def encontrar_pagina_secao_fotos(pdf):
    """Encontra a página onde está a seção 08 - Fotos"""
    for page_num, page in enumerate(pdf.pages, 1):
        texto_pagina = page.extract_text() or ""
        if re.search(r'08\s*[-]?\s*Fotos', texto_pagina, re.IGNORECASE):
            return page_num
    return None

def extrair_todas_fotos_pdf(pdf_path, temp_dir, filename):
    """Extrai TODAS as fotos do PDF de forma abrangente"""
    fotos_extraidas = []
    pdf_name = os.path.splitext(filename)[0]
    fotos_dir = os.path.join(temp_dir, "fotos", pdf_name)
    os.makedirs(fotos_dir, exist_ok=True)
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pagina_inicio_fotos = encontrar_pagina_secao_fotos(pdf)
            paginas_processar = range(len(pdf.pages))
            if pagina_inicio_fotos is not None:
                paginas_processar = range(pagina_inicio_fotos - 1, len(pdf.pages))
            
            for page_num in paginas_processar:
                pagina = pdf.pages[page_num]
                
                if hasattr(pagina, 'images') and pagina.images:
                    altura_pagina = pagina.height
                    largura_pagina = pagina.width
                    
                    for img_idx, img in enumerate(pagina.images):
                        try:
                            y_pos = img['top']
                            x_pos = img['x0']
                            
                            is_logo_top = y_pos < altura_pagina * 0.1
                            is_logo_bottom = y_pos > altura_pagina * 0.9
                            is_logo_corner = (x_pos < largura_pagina * 0.1) or (x_pos > largura_pagina * 0.9)
                            
                            if (is_logo_top and is_logo_corner) or (is_logo_bottom and is_logo_corner):
                                continue
                                
                            if img['width'] < 50 or img['height'] < 50:
                                continue
                                
                            if 'stream' in img:
                                img_data = img['stream'].get_data()
                                if img_data and len(img_data) > 500:
                                    img_name = f"foto_{len(fotos_extraidas) + 1}_pag{page_num + 1}.png"
                                    img_path = os.path.join(fotos_dir, img_name)
                                                                   
                                    with open(img_path, "wb") as f:
                                        f.write(img_data)
                                    
                                    if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                                        try:
                                            with Image.open(img_path) as test_img:
                                                test_img.verify()
                                            fotos_extraidas.append({
                                                'nome': img_name,
                                                'caminho': img_path,
                                                'pagina': page_num + 1
                                            })
                                        except:
                                            os.remove(img_path)
                        except Exception as e:
                            continue
    
    except Exception as e:
        st.error(f"❌ Erro ao abrir PDF {filename}: {str(e)}")
    
    return fotos_extraidas

def melhorar_deteccao_secao_fotos(texto_completo):
    """Melhora a detecção da seção de fotos com padrões mais flexíveis"""
    padroes_fotos = [
        r'08\s*[-]?\s*Fotos',
        r'Seção\s*08.*Fotos',
        r'Fotos',
        r'Imagens',
        r'Documentação\s*Fotográfica'
    ]
    
    for padrao in padroes_fotos:
        match = re.search(padrao, texto_completo, re.IGNORECASE)
        if match:
            return True
    return False

def extrair_texto_entre_parenteses(texto):
    """Extrai exclusivamente o texto entre parênteses da seção Informações Complementares"""
    if not texto or is_empty_info(texto):
        return ''
    
    padrao = r'Informações\s+Complementares\s*:\s*[^(]*\(([^)]+)\)'
    match = re.search(padrao, texto, re.IGNORECASE | re.DOTALL)
    
    if match:
        return clean_text(match.group(1))
    
    return ''

def extrair_data_art(texto):
    """Extrai a data da ART da seção 06 - Documentos Recebidos, item 'Outros'"""
    if not texto or is_empty_info(texto):
        return ''
    
    padrao = r'OUTROS\s*[-\s]*(\d{2}/\d{2}/\d{4})'
    match = re.search(padrao, texto, re.IGNORECASE)
    
    if match:
        data_encontrada = match.group(1)
        try:
            datetime.strptime(data_encontrada, '%d/%m/%Y')
            return data_encontrada
        except ValueError:
            return ''
    
    padrao_alternativo = r'OUTROS[^\d]*(\d{2}/\d{2}/\d{4})'
    match_alt = re.search(padrao_alternativo, texto, re.IGNORECASE)
    
    if match_alt:
        data_encontrada = match_alt.group(1)
        try:
            datetime.strptime(data_encontrada, '%d/%m/%Y')
            return data_encontrada
        except ValueError:
            return ''

def extrair_data_relatorio_anterior(texto):
    """Extrai a data do relatório anterior da seção 07 - Outras Informações"""
    if not texto or is_empty_info(texto):
        return ''
    
    padrao = r'Data\s+do\s+Relat[óo]rio\s+Anterior\s*:\s*(\d{2}/\d{2}/(?:\d{2}|\d{4}))'
    match = re.search(padrao, texto, re.IGNORECASE)
    
    if match:
        data_encontrada = match.group(1)
        if len(data_encontrada) == 8:
            try:
                dia, mes, ano = data_encontrada.split('/')
                ano_completo = f"20{ano}" if len(ano) == 2 else ano
                data_formatada = f"{dia}/{mes}/{ano_completo}"
                datetime.strptime(data_formatada, '%d/%m/%Y')
                return data_formatada
            except ValueError:
                return ''
        elif len(data_encontrada) == 10:
            try:
                datetime.strptime(data_encontrada, '%d/%m/%Y')
                return data_encontrada
            except ValueError:
                return ''
    
    return ''

def extrair_endereco_empreendimento(secao_conteudo):
    """Extrai o endereço completo do empreendimento da seção 01"""
    if not secao_conteudo or is_empty_info(secao_conteudo):
        return ''
    
    linhas = secao_conteudo.split('\n')
    if len(linhas) > 1:
        linhas = linhas[1:]
    
    conteudo_sem_titulo = '\n'.join(linhas)
    padrao_descriptivo = re.compile(r'descriptivo:', re.IGNORECASE)
    match_descriptivo = padrao_descriptivo.search(conteudo_sem_titulo)
    
    if match_descriptivo:
        endereco_texto = conteudo_sem_titulo[:match_descriptivo.start()].strip()
    else:
        endereco_texto = conteudo_sem_titulo.strip()
    
    linhas_endereco = [linha.strip() for linha in endereco_texto.split('\n') if linha.strip()]
    endereco_limpo = ' '.join(linhas_endereco)
    
    if endereco_limpo and not is_empty_info(endereco_limpo):
        return clean_text(endereco_limpo)
    
    return ''

# =================== MÓDULO DE EXTRAÇÃO ===================
def extrair_todos_dados(texto, filename, pdf_path, temp_dir):
    """Extrai todos os dados do PDF de forma estruturada"""
    dados = {
        'RF': '', 'RF Principal': '', 'Situação': '', 'Fiscal': '', 'Supervisão': '', 
        'Data': '', 'Data ART': '', 'Fato Gerador': '', 'Protocolo': '', 'Tipo Visita': '',
        'Endereço Empreendimento - Latitude': '', 'Endereço Empreendimento - Longitude': '',
        'Endereço Empreendimento - Endereço': '', 'Endereço Empreendimento - Descriptivo': '',
        'Identificação do Contratante': '', 'Atividade Desenvolvida': '',
        'Identificação dos Contratados/Responsáveis': '', 'Autuação': '',
        'Documentos Solicitados/Expedidos': '', 'Ofício': 0,
        'Documentos Recebidos': '', 'Resposta Ofício': 0,
        'Outras Informações - Data Relatório Anterior': '',
        'Outras Informações - Informações Complementares': '',
        'Fotos': '', 'Ações': 0, 'Fiscal Nome Completo': '', 'Supervisão Sigla': 'SBXD',
        'Nome Arquivo': filename, 'Fotos Extraídas': 0, 'Regularização': 'NÃO',
        '_Autuações_Count': 0
    }
    
    campos_meta = [
        ('RF', r'Número\s*:\s*([^\n]+)'),
        ('Situação', r'Situação\s*:\s*([^\n]+)'),
        ('Fiscal', r'Agente\s+de\s+Fiscalização\s*:\s*([^\n]+)'),
        ('Supervisão', r'Responsável\s*:\s*([^\n])'),
        ('Data', r'Data\s+Relatório\s*:\s*([^\n]+)'),
        ('Fato Gerador', r'Fato\s+Gerador\s*:\s*([^\n]+)'),
        ('Protocolo', r'Protocolo\s*:\s*([^\n]+)'),
        ('Tipo Visita', r'Tipo\s+Visita\s*:\s*([^\n]+)')
    ]
    
    for campo, padrao in campos_meta:
        match = re.search(padrao, texto)
        if match:
            valor_extraido = clean_text(match.group(1))
            dados[campo] = valor_extraido
            
            if campo == 'Fiscal':
                dados['Fiscal Nome Completo'] = get_nome_completo_agente(valor_extraido)
    
    dados['Protocolo'] = extrair_numero_protocolo(dados['Fato Gerador'])
    dados['RF Principal'] = extrair_rf_principal(texto)
    
    dados['Fiscal'] = formatar_agente_fiscalizacao(dados['Fiscal'])
    dados['Supervisão'] = formatar_responsavel(dados['Supervisão'])
    dados['Data'] = formatar_data_relatorio(dados['Data'])
    
    secoes = [
        ("01 - Endereço Empreendimento", None),
        ("02 - Identificação do Contratante do Empreendimento", 'Identificação do Contratante'),
        ("03 - Atividade Desenvolvida", 'Atividade Desenvolvida'),
        ("04 - Identificação dos Contratados, Responsáveis Técnicos e/ou Fiscalizados", 'Identificação dos Contratados/Responsáveis'),
        ("05 - Documentos Solicitados / Expedidos", 'Documentos Solicitados/Expedidos'),
        ("06 - Documentos Recebidos", 'Documentos Recebidos'),
        ("07 - Outras Informações", None)
    ]
    
    for secao_nome, campo_dados in secoes:
        secao_conteudo = extrair_secao(texto, secao_nome)
        if secao_conteudo:
            if campo_dados:
                dados[campo_dados] = clean_text(secao_conteudo)
            
            if secao_nome == "01 - Endereço Empreendimento":
                dados['Endereço Empreendimento - Endereço'] = extrair_endereco_empreendimento(secao_conteudo)
                
                lat_match = re.search(r'Latitude\s*:\s*([-\d,.]+)', secao_conteudo)
                long_match = re.search(r'Longitude\s*:\s*([-\d,.]+)', secao_conteudo)
                if lat_match:
                    dados['Endereço Empreendimento - Latitude'] = clean_text(lat_match.group(1))
                if long_match:
                    dados['Endereço Empreendimento - Longitude'] = clean_text(long_match.group(1))
                
                padrao_descriptivo = re.compile(r'descriptivo:', re.IGNORECASE)
                match_descriptivo = padrao_descriptivo.search(secao_conteudo)
                if match_descriptivo:
                    desc_part = secao_conteudo[match_descriptivo.end():]
                    desc_text = clean_text(desc_part)
                    if desc_text:
                        dados['Endereço Empreendimento - Descriptivo'] = desc_text
            
            elif secao_nome == "04 - Identificação dos Contratados, Responsáveis Técnicos e/ou Fiscalizados":
                dados['Autuação'] = extrair_numero_autuacao(secao_conteudo)
                ramos_atividade_count = contar_ramos_atividade_secao_04(texto)
                dados['Ações'] = ramos_atividade_count
                autuacoes_count = contar_autuacoes_secao_04(texto)
                dados['_Autuações_Count'] = autuacoes_count
            
            elif secao_nome == "05 - Documentos Solicitados / Expedidos":
                conteudo = secao_conteudo.split("Fonte Informação")[0].strip()
                dados['Documentos Solicitados/Expedidos'] = clean_text(conteudo)
                dados['Ofício'] = verificar_oficio(conteudo)
            
            elif secao_nome == "06 - Documentos Recebidos":
                dados['Data ART'] = extrair_data_art(secao_conteudo)
                dados['Resposta Ofício'] = verificar_resposta_oficio(secao_conteudo)
            
            elif secao_nome == "07 - Outras Informações":
                dados['Outras Informações - Informações Complementares'] = extrair_texto_entre_parenteses(secao_conteudo)
                dados['Outras Informações - Data Relatório Anterior'] = extrair_data_relatorio_anterior(secao_conteudo)
    
    try:
        data_art = dados['Data ART']
        data_relatorio_anterior = dados['Outras Informações - Data Relatório Anterior']
        
        if data_art and data_relatorio_anterior:
            data_art_dt = datetime.strptime(data_art, '%d/%m/%Y')
            data_rel_ant_dt = datetime.strptime(data_relatorio_anterior, '%d/%m/%Y')
            
            if data_art_dt >= data_rel_ant_dt:
                dados['Regularização'] = 'SIM'
            else:
                dados['Regularização'] = 'NÃO'
    except:
        pass
    
    tem_secao_fotos = melhorar_deteccao_secao_fotos(texto)
    fotos_extraidas = extrair_todas_fotos_pdf(pdf_path, temp_dir, filename)
    dados['Fotos Extraídas'] = len(fotos_extraidas)
    
    if fotos_extraidas:
        dados['Fotos'] = f"{len(fotos_extraidas)} foto(s) extraída(s)"
    else:
        dados['Fotos'] = "Nenhuma foto extraída"
    
    return dados

# =================== GERADORES DE RELATÓRIO ===================
def calcular_pontuacao_por_status(status_fotos, acoes, oficios, resposta_oficios, protocolos, autuacoes, regularizacao):
    """Calcula a pontuação baseada no status das fotos"""
    if status_fotos == 'SIM':
        pontuacao = TABELA_PONTUACAO['SIM']
    else:
        pontuacao = TABELA_PONTUACAO['NÃO']
    
    total = (
        pontuacao['RFs'] +
        (pontuacao['Ações'] * acoes) +
        (pontuacao['Ofícios'] * oficios) +
        (pontuacao['Resposta Ofícios'] * resposta_oficios) +
        (pontuacao['Protocolos'] * protocolos) +
        pontuacao['Fotos'] +
        (pontuacao['Regularização'] if regularizacao == 'SIM' else 0)
    )
    
    return total

def gerar_relatorio_completo(df):
    """Gera PDF com todos os dados extraídos"""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    try:
        logo_path = "10.png"
        if os.path.exists(logo_path):
            pdf.image(logo_path, x=50, y=10, w=110)
            pdf.ln(40)
    except:
        pass
    
    pdf.set_font('Arial', 'B', 16)
    pdf.cell(0, 10, 'Relatório Completo de Fiscalização', 0, 1, 'C')
    
    pdf.set_font('Arial', '', 12)
    nome_completo_agente = df.iloc[0]['Fiscal Nome Completo'] if 'Fiscal Nome Completo' in df.columns and len(df) > 0 else ''
    pdf.cell(0, 10, f'Agente de Fiscalização: {nome_completo_agente}', 0, 1)
    pdf.cell(0, 10, 'Supervisão: SBXD', 0, 1)
    
    if len(df) > 0:
        try:
            datas = pd.to_datetime(df['Data'], errors='coerce', dayfirst=True)
            datas_validas = datas[~datas.isna()]
            if not datas_validas.empty:
                primeira_data = datas_validas.min().strftime('%d/%m/%Y')
                ultima_data = datas_validas.max().strftime('%d/%m/%Y')
                pdf.cell(0, 10, f'Período: {primeira_data} a {ultima_data}', 0, 1)
        except:
            pass
    
    pdf.cell(0, 10, f'Gerado em: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}', 0, 1)
    pdf.ln(10)
    
    colunas = ['RFs', 'RF Principal', 'Data ART', 'Regularização', 'Data', 'Ações', 'Ofícios', 'Resposta Ofícios', 'Protocolos', 'Autuações', 'Fotos', 'Pontuação']
    col_widths = [20, 25, 18, 18, 15, 10, 10, 24, 15, 15, 10, 15]
    
    pdf.set_font('Arial', 'B', 7)
    for i in range(len(colunas)):
        pdf.cell(col_widths[i], 8, colunas[i], 1, 0, 'C')
    pdf.ln()
    
    pdf.set_font('Arial', '', 7)
    df_validos = df[df['RF'] != 'TOTAL'] if 'TOTAL' in df['RF'].values else df
    num_registros = len(df_validos)
    
    total_acoes = 0
    total_oficios = 0
    total_resposta_oficios = 0
    total_protocolos = 0
    total_autuacoes = 0
    total_fotos = 0
    total_legalizacoes = 0
    total_pontuacao = 0
    total_sim = 0
    total_nao = 0

    for _, row in df_validos.iterrows():
        rf_text = str(row['RF'])[:15] + '...' if len(str(row['RF'])) > 15 else str(row['RF'])
        pdf.cell(col_widths[0], 8, rf_text, 1, 0, 'C')
        
        rf_principal_text = str(row['RF Principal'])[:15] + '...' if len(str(row['RF Principal'])) > 15 else str(row['RF Principal'])
        pdf.cell(col_widths[1], 8, rf_principal_text, 1, 0, 'C')
        
        data_art_text = str(row['Data ART'])[:10] if row['Data ART'] and str(row['Data ART']).strip() != '' else ''
        pdf.cell(col_widths[2], 8, data_art_text, 1, 0, 'C')
        
        regularizacao = str(row['Regularização']) if 'Regularização' in row else 'NÃO'
        pdf.cell(col_widths[3], 8, regularizacao, 1, 0, 'C')
        
        pdf.cell(col_widths[4], 8, str(row['Data']), 1, 0, 'C')
        
        acoes = row['Ações'] if pd.notna(row['Ações']) else 0
        pdf.cell(col_widths[5], 8, str(acoes), 1, 0, 'C')
        
        oficios = row['Ofício'] if pd.notna(row['Ofício']) else 0
        pdf.cell(col_widths[6], 8, str(oficios), 1, 0, 'C')
        
        resposta_oficios = row['Resposta Ofício'] if pd.notna(row['Resposta Ofício']) else 0
        pdf.cell(col_widths[7], 8, str(resposta_oficios), 1, 0, 'C')
        
        tem_protocolo = 1 if row['Protocolo'] and str(row['Protocolo']).strip() != '' else 0
        pdf.cell(col_widths[8], 8, str(tem_protocolo), 1, 0, 'C')
        
        autuacoes_count = 0
        if '_Autuações_Count' in row and pd.notna(row['_Autuações_Count']) and str(row['_Autuações_Count']).strip() != '':
            try:
                autuacoes_count = int(row['_Autuações_Count'])
            except (ValueError, TypeError):
                autuacoes_count = 0
        else:
            tem_autuacao = '1' if row['Autuação'] and str(row['Autuação']).strip() != '' else '0'
            autuacoes_count = 1 if tem_autuacao == '1' else 0
        
        pdf.cell(col_widths[9], 8, str(autuacoes_count), 1, 0, 'C')
        
        tem_fotos = 'SIM' if row['Fotos Extraídas'] > 0 else 'NÃO'
        pdf.cell(col_widths[10], 8, tem_fotos, 1, 0, 'C')
        
        pontuacao = calcular_pontuacao_por_status(
            tem_fotos, 
            acoes, 
            oficios, 
            resposta_oficios, 
            tem_protocolo, 
            autuacoes_count,
            regularizacao
        )
        pdf.cell(col_widths[11], 8, f"{pontuacao:.2f}", 1, 0, 'C')
        
        pdf.ln()
        
        total_acoes += acoes
        total_oficios += oficios
        total_resposta_oficios += resposta_oficios
        total_protocolos += tem_protocolo
        total_autuacoes += autuacoes_count
        total_fotos += 1 if tem_fotos == 'SIM' else 0
        total_legalizacoes += 1 if regularizacao == 'SIM' else 0
        total_pontuacao += pontuacao
        
        if tem_fotos == 'SIM':
            total_sim += pontuacao
        else:
            total_nao += pontuacao
    
    pdf.set_font('Arial', 'B', 7)
    pdf.cell(col_widths[0], 8, f"TOTAL ({num_registros})", 1, 0, 'C')
    pdf.cell(col_widths[1], 8, "", 1, 0, 'C')
    pdf.cell(col_widths[2], 8, "", 1, 0, 'C')
    pdf.cell(col_widths[3], 8, str(total_legalizacoes), 1, 0, 'C')
    pdf.cell(col_widths[4], 8, "", 1, 0, 'C')
    pdf.cell(col_widths[5], 8, str(total_acoes), 1, 0, 'C')
    pdf.cell(col_widths[6], 8, str(total_oficios), 1, 0, 'C')
    pdf.cell(col_widths[7], 8, str(total_resposta_oficios), 1, 0, 'C')
    pdf.cell(col_widths[8], 8, str(total_protocolos), 1, 0, 'C')
    pdf.cell(col_widths[9], 8, str(total_autuacoes), 1, 0, 'C')
    pdf.cell(col_widths[10], 8, str(total_fotos), 1, 0, 'C')
    pdf.cell(col_widths[11], 8, f"{total_pontuacao:.2f}", 1, 0, 'C')
    pdf.ln()
    
    pdf.ln(10)
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, 'Informações Complementares', 0, 1, 'C')
    pdf.ln(5)
    
    pdf.set_font('Arial', '', 12)
    df_validos = df[df['RF'] != 'TOTAL'] if 'TOTAL' in df['RF'].values else df
    
    tem_informacoes_complementares = False
    
    for _, row in df_validos.iterrows():
        if row['RF'] and str(row['RF']).strip():
            if row['Outras Informações - Informações Complementares'] and str(row['Outras Informações - Informações Complementares']).strip():
                tem_informacoes_complementares = True
                
                pdf.set_font('Arial', 'B', 12)
                pdf.cell(30, 10, 'RF:', 0, 0)
                pdf.set_font('Arial', '', 12)
                pdf.cell(0, 10, str(row['RF']), 0, 1)
                
                info_complementares = str(row['Outras Informações - Informações Complementares'])
                pdf.multi_cell(0, 8, info_complementares)
                
                pdf.ln(5)
    
    if not tem_informacoes_complementares:
        pdf.set_font('Arial', '', 12)
        pdf.cell(0, 10, 'Nenhuma informação complementar disponível.', 0, 1, 'C')
    
    pdf.ln(10)
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, 'Resumo de Pontuação por Status de Fotos', 0, 1, 'C')
    pdf.ln(5)
    
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(60, 10, 'Status Fotos', 1, 0, 'C')
    pdf.cell(60, 10, 'Quantidade', 1, 0, 'C')
    pdf.cell(60, 10, 'Pontuação Total', 1, 1, 'C')
    
    pdf.set_font('Arial', '', 12)
    pdf.cell(60, 10, 'SIM', 1, 0, 'C')
    pdf.cell(60, 10, str(total_fotos), 1, 0, 'C')
    pdf.cell(60, 10, f"{total_sim:.2f}", 1, 1, 'C')
    
    pdf.cell(60, 10, 'NÃO', 1, 0, 'C')
    pdf.cell(60, 10, str(num_registros - total_fotos), 1, 0, 'C')
    pdf.cell(60, 10, f"{total_nao:.2f}", 1, 1, 'C')
    
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(60, 10, 'TOTAL', 1, 0, 'C')
    pdf.cell(60, 10, str(num_registros), 1, 0, 'C')
    pdf.cell(60, 10, f"{total_pontuacao:.2f}", 1, 1, 'C')
    
    return pdf.output(dest='S').encode('latin1')

# =================== MÓDULO PRINCIPAL ===================
def extrator_pdf_consolidado():
    st.title("Leitura dos RFs, extração dos dados, geração de planilha excel e produção de Relatórios em PDF.")
    st.markdown("""
    **Extrai todos os dados dos PDFs para uma planilha Excel com formatação específica:**
    - Faz a leitura dos RFs em PDF e extrai todos os dados, gerando uma planilha excel.
    - Produz um relatório em PDF com os dados solicitados previamente.
    """)

    uploaded_files = st.file_uploader("Selecione os PDFs para extração", type="pdf", accept_multiple_files=True)
    
    if uploaded_files:
        temp_dir = criar_temp_dir()
        try:
            with st.spinner("Processando arquivos..."):
                dados_completos = []
                todas_fotos = []
                
                for file in uploaded_files:
                    temp_path = os.path.join(temp_dir, file.name)
                    with open(temp_path, "wb") as f:
                        f.write(file.getbuffer())
                    
                    with pdfplumber.open(temp_path) as pdf:
                        texto = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    
                    dados = extrair_todos_dados(texto, file.name, temp_path, temp_dir)
                    dados_completos.append(dados)
                    
                    fotos_dir = os.path.join(temp_dir, "fotos", os.path.splitext(file.name)[0])
                    if os.path.exists(fotos_dir) and os.listdir(fotos_dir):
                        todas_fotos.append(fotos_dir)
                    
                    os.unlink(temp_path)
                
                if not dados_completos:
                    st.error("Nenhum dado foi extraído dos arquivos.")
                    return
                
                df_completo = pd.DataFrame(dados_completos).fillna('')
                
                colunas = list(df_completo.columns)
                idx_data = colunas.index('Data')
                if 'Data ART' in colunas:
                    colunas.insert(idx_data + 1, colunas.pop(colunas.index('Data ART')))
                if 'RF Principal' in colunas:
                    colunas.insert(idx_data + 2, colunas.pop(colunas.index('RF Principal')))
                if 'Regularização' in colunas:
                    colunas.insert(idx_data + 3, colunas.pop(colunas.index('Regularização')))
                
                df_completo = df_completo[colunas]
                
                df_total = pd.DataFrame([{
                    'RF': 'TOTAL',
                    'Ações': df_completo['Ações'].sum(),
                    'Ofício': df_completo['Ofício'].sum(),
                    'Resposta Ofício': df_completo['Resposta Ofício'].sum()
                }])
                df_completo = pd.concat([df_completo, df_total], ignore_index=True)
                
                with st.expander("Visualizar dados extraídos", expanded=True):
                    st.dataframe(df_completo)
                
                pdf_completo = gerar_relatorio_completo(df_completo)
                
                excel_buffer = BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    df_completo.to_excel(writer, sheet_name='Dados Completos', index=False)
                    
                    colunas_resumo = ['RF', 'RF Principal', 'Fiscal', 'Supervisão', 'Data', 'Data ART', 'Regularização', 'Fato Gerador', 'Protocolo', 
                                     'Endereço Empreendimento - Endereço', 'Identificação dos Contratados/Responsáveis', 'Autuação', 'Ações', 'Ofício', 'Resposta Ofício', 'Fotos']
                    df_resumo = df_completo[[col for col in colunas_resumo if col in df_completo.columns]]
                    df_resumo.to_excel(writer, sheet_name='Resumo', index=False)
                
                st.success("Extração concluída com sucesso!")
                
                st.download_button(
                    "⬇️ Baixar Excel Completo",
                    excel_buffer.getvalue(),
                    "dados_completos.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
                st.download_button(
                    "⬇️ Baixar Relatório Completo em PDF",
                    pdf_completo,
                    "relatorio_completo.pdf",
                    "application/pdf"
                )
                
                if todas_fotos:
                    zip_path = os.path.join(temp_dir, "fotos_extraidas.zip")
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for foto_dir in todas_fotos:
                            for root, _, files in os.walk(foto_dir):
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    arcname = os.path.relpath(file_path, os.path.join(temp_dir, "fotos"))
                                    zipf.write(file_path, arcname)
                    
                    with open(zip_path, "rb") as f:
                        foto_zip = f.read()
                    
                    st.download_button(
                        "⬇️ Baixar Fotos Extraídas (ZIP)",
                        foto_zip,
                        "fotos_extraidas.zip",
                        "application/zip"
                    )
                else:
                    st.info("Nenhuma foto foi extraída dos PDFs processados.")
        
        except Exception as e:
            st.error(f"Erro durante o processamento: {str(e)}")
        finally:
            limpar_temp_dir(temp_dir)

# =================== INTERFACE PRINCIPAL ===================
def main():
    try:
        logo = Image.open("10.png")
    except:
        logo = None
    
    col1, col2 = st.columns([1, 2])
    with col1:
        if logo: 
            st.image(logo, width=400)
    with col2:
        st.title("CREA-RJ - Conselho Regional de Engenharia e Agronomia do Rio de Janeiro")
    
    st.markdown("")
    extrator_pdf_consolidado()
    st.markdown("2025 - Carlos Franklin")

if __name__ == "__main__":
    main()