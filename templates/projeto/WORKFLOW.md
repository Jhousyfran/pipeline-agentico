---
repo: {{repo}}
branch_base: main
prefixo: {{prefixo}}
gate_stg: echo "defina o gate_stg (build, lint, testes unitários e de integração)" && exit 1
gate_qa: echo "defina o gate_qa (testes end to end, ex.: npx playwright test)" && exit 1
---

# WORKFLOW · {{nome}}

Quem faz o quê, em que ordem, e o que cada gate valida.

## Atores e entregas

| Etapa | Papel | Entrega | Arquivo no vault |
|---|---|---|---|
| analise | analista | critérios fechados e tasks | analise.md |
| desenho | arquiteto | decisão de desenho e o porquê | adr.md |
| codigo | dev | implementação com testes | plano.md + commit |
| revisao | revisor | aprova ou devolve o diff | revisao.md |
| gate_stg | código | build, lint, unitários e integração | evidencias/gate_stg.log |
| qa | qa | um teste e2e por critério de aceite | testes.md + commit |
| gate_qa | código | roda os testes e2e | evidencias/gate_qa.log |

## Gates

Os comandos do frontmatter rodam na worktree do card, sem inferência. Código de saída 0 aprova;
qualquer outro devolve o card ao dev com o final do log.

## Convenções de teste

<!-- Onde ficam os testes unitários, de integração e e2e; como rodar localmente. -->
