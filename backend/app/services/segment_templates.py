"""
Serviço de Modelos por Segmento (Item F1.4 do Plano Presenthia).
Aplica configurações pré-moldadas de tom de voz, funil, perguntas de triagem
e mensagens prontas ao tenant em 1 clique.
"""

TEMPLATES = {
    "estetica": {
        "nome": "Salão, Barbearia & Estética",
        "icone": "✂️",
        "descricao": "Ideal para agendamento de serviços, controle de cadeiras e confirmação automática de horários.",
        "prompt_ia": (
            "Você é a recepcionista virtual de um espaço de beleza e estética. "
            "Seu objetivo é ser calorosa, elegante e conduzir o cliente a escolher o serviço desejado, "
            "o profissional de preferência e agendar o melhor horário. Confirme sempre a duração do procedimento."
        ),
        "perguntas_qualificacao": [
            "Qual serviço você gostaria de realizar hoje (cabelo, barba, unhas ou estética)?",
            "Você possui algum profissional de sua preferência?",
            "Qual o melhor período para o seu atendimento (manhã ou tarde)?"
        ],
        "etapas_funil": [
            {"nome": "Novo Contato", "ordem": 1},
            {"nome": "Interesse Demonstrado", "ordem": 2},
            {"nome": "Horário Reservado", "ordem": 3},
            {"nome": "Atendimento Realizado", "ordem": 4},
            {"nome": "Pós-Venda / Recompra", "ordem": 5}
        ],
        "mensagem_boas_vindas": "Olá! Seja bem-vindo(a). Como posso realçar o seu visual e bem-estar hoje?"
    },
    "clinica": {
        "nome": "Clínica, Consultório & Saúde",
        "icone": "🩺",
        "descricao": "Focado em triagem respeitosa, agendamento de consultas/exames e orientações pré-atendimento sem diagnóstico.",
        "prompt_ia": (
            "Você é o assistente virtual de uma clínica de saúde. Atue com respeito, empatia e clareza. "
            "Você nunca emite diagnósticos médicos nem receita medicamentos. Conduza a triagem para especialidade correta, "
            "verifique se o atendimento é particular ou por convênio e confirme o horário."
        ),
        "perguntas_qualificacao": [
            "Qual a especialidade médica ou tipo de consulta você procura?",
            "O atendimento será particular ou por convênio médico?",
            "Você já é paciente da clínica ou é sua primeira consulta?"
        ],
        "etapas_funil": [
            {"nome": "Triagem Inicial", "ordem": 1},
            {"nome": "Convênio / Particular Validado", "ordem": 2},
            {"nome": "Consulta Agendada", "ordem": 3},
            {"nome": "Lembrete Enviado", "ordem": 4},
            {"nome": "Atendido / Retorno", "ordem": 5}
        ],
        "mensagem_boas_vindas": "Olá! Bem-vindo(a) à nossa clínica. Como posso auxiliar na sua saúde e agendamento?"
    },
    "tecnico": {
        "nome": "Serviços Técnicos & Manutenção",
        "icone": "🔧",
        "descricao": "Coleta rápida de modelo de equipamento, relato do defeito, endereço e urgência para visita técnica.",
        "prompt_ia": (
            "Você é o assistente técnico comercial da empresa. Seja direto, prático e atencioso. "
            "Pergunte qual é o equipamento com defeito, a marca/modelo, o sintoma apresentado e o endereço da visita."
        ),
        "perguntas_qualificacao": [
            "Qual é o equipamento ou serviço que necessita de suporte?",
            "Qual é a marca, modelo e o defeito apresentado?",
            "Em qual bairro ou cidade deve ser realizada a visita técnica?"
        ],
        "etapas_funil": [
            {"nome": "Orçamento Solicitado", "ordem": 1},
            {"nome": "Diagnóstico Técnico", "ordem": 2},
            {"nome": "Orçamento Enviado", "ordem": 3},
            {"nome": "Visita / Serviço Agendado", "ordem": 4},
            {"nome": "Serviço Concluído", "ordem": 5}
        ],
        "mensagem_boas_vindas": "Olá! Precisa de assistência técnica ou manutenção? Conte-me qual equipamento precisa de reparo."
    },
    "b2b": {
        "nome": "B2B, Consultoria & Projetos",
        "icone": "💼",
        "descricao": "Qualificação profunda de tomadores de decisão, tamanho da empresa e agendamento de reuniões estratégicas.",
        "prompt_ia": (
            "Você é o consultor de negócios corporativo. Seu tom é executivo, focado em ROI, produtividade e resolução de dores. "
            "Qualifique o porte da empresa, o cargo da pessoa e o principal desafio enfrentado antes de direcionar à agenda executiva."
        ),
        "perguntas_qualificacao": [
            "Qual é a razão social da sua empresa e o seu cargo?",
            "Qual o principal objetivo ou gargalo que sua equipe busca resolver atualmente?",
            "Quantos colaboradores ou unidades estarão envolvidos no projeto?"
        ],
        "etapas_funil": [
            {"nome": "Lead Qualificado (MQL)", "ordem": 1},
            {"nome": "Reunião de Diagnóstico", "ordem": 2},
            {"nome": "Proposta Comercial", "ordem": 3},
            {"nome": "Em Negociação", "ordem": 4},
            {"nome": "Contrato Fechado", "ordem": 5}
        ],
        "mensagem_boas_vindas": "Olá! Seja bem-vindo(a). Como nossa consultoria estratégica pode impulsionar sua operação?"
    }
}

def listar_templates():
    return [
        {
            "id": k,
            "nome": v["nome"],
            "icone": v["icone"],
            "descricao": v["descricao"],
            "perguntas": v["perguntas_qualificacao"],
            "etapas": [e["nome"] for e in v["etapas_funil"]],
            "mensagem_boas_vindas": v["mensagem_boas_vindas"]
        }
        for k, v in TEMPLATES.items()
    ]

def obter_template(template_id: str):
    return TEMPLATES.get(template_id)
