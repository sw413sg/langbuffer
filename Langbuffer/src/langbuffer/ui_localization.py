"""Per-window interface translations. Subtitle content is never localized here."""
from __future__ import annotations

import re
import string
from types import FunctionType, MethodType

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget, QLabel, QAbstractButton, QComboBox, QMenu, QLineEdit
from shiboken6 import isValid


SUPPORTED_LOCALES = ('en', 'es', 'pt', 'fr')

# English is the canonical key. Spanish aliases cover the inherited stable
# widgets; Portuguese/French aliases allow already rendered text to be rebound.
_ROWS = r'''
{action}: {name} | {action}: {name} | {action}: {name} | {action} : {name}
Settings | Configuración | Configurações | Paramètres
Subtitles | Subtítulos | Legendas | Sous-titres
Language | Idioma | Idioma | Langue
Languages | Idiomas | Idiomas | Langues
Language manager | Gestor de idiomas | Gerenciador de idiomas | Gestionnaire de langues
Original language | Idioma original | Idioma original | Langue d’origine
Translate to | Traducción a | Tradução para | Traduire vers
Translation engine | Motor de traducción | Motor de tradução | Moteur de traduction
For English audio when you prefer the larger model. | Para audio en inglés si prefieres el modelo más grande. | Para áudio em inglês se preferir o modelo maior. | Pour l’audio en anglais si vous préférez le modèle plus grand.
For English audio when you need a lighter model. | Para audio en inglés si necesitas un modelo más ligero. | Para áudio em inglês se precisar de um modelo mais leve. | Pour l’audio en anglais si vous avez besoin d’un modèle plus léger.
For audio in other input languages; selected automatically. | Para audio en otros idiomas de entrada; se selecciona automáticamente. | Para áudio nos outros idiomas de entrada; selecionado automaticamente. | Pour l’audio dans les autres langues d’entrée ; sélectionné automatiquement.
Input | Entrada | Entrada | Entrée
Output | Salida | Saída | Sortie
Input language | Idioma de entrada | Idioma de entrada | Langue d’entrée
Output language | Idioma de salida | Idioma de saída | Langue de sortie
English | Inglés | Inglês | Anglais
Spanish | Español | Espanhol | Espagnol
Portuguese | Portugués | Português | Portugais
French | Francés | Francês | Français
German | Alemán | Alemão | Allemand
Italian | Italiano | Italiano | Italien
Japanese | Japonés | Japonês | Japonais
Korean | Coreano | Coreano | Coréen
Simplified Chinese | Chino simplificado | Chinês simplificado | Chinois simplifié
Content link | Enlace del contenido | Link do conteúdo | Lien du contenu
Paste a direct YouTube Live or Facebook video link. | Pega el enlace directo a la emisión de YouTube o al video de Facebook. | Cole o link direto da transmissão do YouTube ou do vídeo do Facebook. | Collez le lien direct du direct YouTube ou de la vidéo Facebook.
YouTube Live, Facebook, X, Twitch or Kick link | Enlace de YouTube Live, Facebook, X, Twitch o Kick | Link do YouTube Live, Facebook, X, Twitch ou Kick | Lien YouTube Live, Facebook, X, Twitch ou Kick
This stream has not started yet. | Este directo todavía no comenzó. | Esta transmissão ainda não começou. | Ce direct n’a pas encore commencé.
This YouTube stream has ended. | Este directo de YouTube terminó. | Esta transmissão do YouTube terminou. | Ce direct YouTube est terminé.
YouTube support is limited to active live streams. | YouTube admite únicamente directos activos. | O YouTube aceita apenas transmissões ao vivo ativas. | YouTube prend uniquement en charge les directs actifs.
This content requires a login. Use a public link. | Este contenido requiere iniciar sesión. Usa un enlace público. | Este conteúdo exige login. Use um link público. | Ce contenu nécessite une connexion. Utilisez un lien public.
The YouTube JavaScript runtime is missing. | Falta el runtime JavaScript de YouTube. | Falta o runtime JavaScript do YouTube. | Le runtime JavaScript de YouTube est absent.
The YouTube EJS package is missing or incompatible. | El paquete EJS de YouTube falta o es incompatible. | O pacote EJS do YouTube está ausente ou é incompatível. | Le paquet EJS de YouTube est absent ou incompatible.
Facebook live or video state could not be verified. | No se pudo verificar si el contenido de Facebook es un directo o un video. | Não foi possível verificar se o conteúdo do Facebook é ao vivo ou um vídeo. | Impossible de vérifier si le contenu Facebook est un direct ou une vidéo.
The stream did not publish new segments during verification. | El directo no publicó segmentos nuevos durante la comprobación. | A transmissão não publicou novos segmentos durante a verificação. | Le direct n’a pas publié de nouveaux segments pendant la vérification.
The audio and video tracks could not be aligned. | No se pudieron alinear las pistas de audio y video. | Não foi possível alinhar as faixas de áudio e vídeo. | Impossible d’aligner les pistes audio et vidéo.
These separate audio/video tracks are not supported. | Estas pistas separadas de audio y video no son compatibles. | Estas faixas separadas de áudio e vídeo não são compatíveis. | Ces pistes audio et vidéo séparées ne sont pas prises en charge.
This source only offers an unsupported DASH format. | Esta fuente solo ofrece un formato DASH no compatible. | Esta fonte oferece apenas um formato DASH incompatível. | Cette source propose uniquement un format DASH non pris en charge.
This video server does not support the required seeking. | El servidor del video no admite el acceso por rangos necesario. | O servidor de vídeo não aceita o acesso por intervalos necessário. | Le serveur vidéo ne prend pas en charge l’accès par plages requis.
Checking this source timed out. Try again. | Se agotó el tiempo de consulta de la fuente. Inténtalo de nuevo. | O tempo de consulta da fonte se esgotou. Tente novamente. | La vérification de cette source a expiré. Réessayez.
The source could not be accessed. Check the public link and try again. | No se pudo acceder a la fuente. Revisa el enlace público e inténtalo de nuevo. | Não foi possível acessar a fonte. Verifique o link público e tente novamente. | Impossible d’accéder à la source. Vérifiez le lien public et réessayez.
The source returned a different video. Playback was stopped. | La fuente devolvió otro video. Se detuvo la reproducción. | A fonte retornou outro vídeo. A reprodução foi interrompida. | La source a renvoyé une autre vidéo. La lecture a été arrêtée.
No supported audio/video format is available for this source. | Esta fuente no ofrece un formato de audio y video compatible. | Esta fonte não oferece um formato de áudio e vídeo compatível. | Cette source ne propose aucun format audio et vidéo compatible.
Paste a Kick/Twitch stream or an X video | Pega un directo de Kick/Twitch o un video de X | Cole uma transmissão do Kick/Twitch ou um vídeo do X | Collez un direct Kick/Twitch ou une vidéo X
You can edit the link during playback. Press Restart to apply it. | El enlace puede editarse durante la reproducción. Pulsa Reiniciar para aplicarlo. | Você pode editar o link durante a reprodução. Pressione Reiniciar para aplicá-lo. | Vous pouvez modifier le lien pendant la lecture. Appuyez sur Redémarrer pour l’appliquer.
Recognition | Reconocimiento | Reconhecimento | Reconnaissance
Silent output | Salida silenciosa | Saída silenciosa | Sortie silencieuse
Listen through | Escuchar por | Ouvir por | Sortie audio
Delay | Retraso | Atraso | Délai
Delay in seconds | Retraso en segundos | Atraso em segundos | Délai en secondes
Start | Iniciar | Iniciar | Démarrer
Restart | Reiniciar | Reiniciar | Redémarrer
Automatic | Automático | Automático | Automatique
White | Blanco | Branco | Blanc
Yellow | Amarillo | Amarelo | Jaune
Red | Rojo | Vermelho | Rouge
Black | Negro | Preto | Noir
Gray | Gris | Cinza | Gris
Left | Izquierda | Esquerda | Gauche
Center | Centro | Centro | Centre
Right | Derecha | Direita | Droite
Size | Tamaño | Tamanho | Taille
Font | Fuente | Fonte | Police
Text color | Color del texto | Cor do texto | Couleur du texte
Original text color | Color del original | Cor do original | Couleur du texte original
Background color | Color del fondo | Cor do fundo | Couleur du fond
Background opacity | Opacidad del fondo | Opacidade do fundo | Opacité du fond
Line width | Ancho de línea | Largura da linha | Largeur des lignes
Alignment | Alineación | Alinhamento | Alignement
Horizontal position | Posición horizontal | Posição horizontal | Position horizontale
Vertical position | Posición vertical | Posição vertical | Position verticale
Offset | Desfase | Deslocamento | Décalage
Subtitle offset | Desfase de subtítulos | Deslocamento das legendas | Décalage des sous-titres
Negative values move left; positive values move right. | Valores negativos mueven a la izquierda; positivos, a la derecha. | Valores negativos movem para a esquerda; positivos, para a direita. | Les valeurs négatives déplacent vers la gauche ; les positives, vers la droite.
Negative values move down; positive values move up. | Valores negativos mueven hacia abajo; positivos, hacia arriba. | Valores negativos movem para baixo; positivos, para cima. | Les valeurs négatives déplacent vers le bas ; les positives, vers le haut.
Also show the original | Mostrar también el original | Mostrar também o original | Afficher aussi l’original
Also show the original in English | Mostrar también el original en inglés | Mostrar também o original em inglês | Afficher aussi l’original en anglais
Also show the original in {language} | Mostrar también el original en {language} | Mostrar também o original em {language} | Afficher aussi l’original en {language}
Keep playback always on top | Mantener la reproducción siempre visible | Manter a reprodução sempre visível | Garder la lecture au premier plan
While this panel is open, the video shows a sample. Close it to return to live subtitles. | Mientras esta ventana esté abierta, el video muestra un ejemplo. Al cerrarla vuelven los subtítulos en directo. | Enquanto este painel estiver aberto, o vídeo mostra um exemplo. Feche-o para voltar às legendas ao vivo. | Tant que ce panneau est ouvert, la vidéo affiche un exemple. Fermez-le pour revenir aux sous-titres en direct.
Stop and close | Detener y cerrar | Parar e fechar | Arrêter et fermer
Minimize | Minimizar | Minimizar | Réduire
Enable Windows frame | Activar marco de Windows | Ativar moldura do Windows | Activer le cadre Windows
Remove Windows frame | Quitar marco de Windows | Remover moldura do Windows | Retirer le cadre Windows
Stop | Detener | Parar | Arrêter
Listening volume | Volumen de escucha | Volume de escuta | Volume audio
Resume | Reanudar | Retomar | Reprendre
Pause | Pausar | Pausar | Mettre en pause
Unmute | Activar sonido | Ativar som | Activer le son
Mute | Silenciar | Silenciar | Couper le son
Exit full screen | Salir de pantalla completa | Sair da tela cheia | Quitter le plein écran
Full screen | Pantalla completa | Tela cheia | Plein écran
Hide panel | Ocultar panel | Ocultar painel | Masquer le panneau
Hide panel · Esc | Ocultar panel · Esc | Ocultar painel · Esc | Masquer le panneau · Échap
Preparing video… | Preparando video… | Preparando vídeo… | Préparation de la vidéo…
Paused | En pausa | Em pausa | En pause
Playing video… | Mostrando video… | Reproduzindo vídeo… | Lecture de la vidéo…
Buffering and translating now... {seconds} s | Almacenando en buffer y traduciendo ahora... {seconds} s | Armazenando em buffer e traduzindo agora... {seconds} s | Mise en mémoire tampon et traduction en cours... {seconds} s
Twitch ad detected. Reconnecting{countdown} | Anuncio de Twitch detectado. Intentando reconectar{countdown} | Anúncio da Twitch detectado. Reconectando{countdown} | Publicité Twitch détectée. Reconnexion{countdown}
Reconnecting to Twitch… | Reconectando con Twitch… | Reconectando à Twitch… | Reconnexion à Twitch…
Twitch ad break · paused. Press Resume to continue. | Publicidad de Twitch · en pausa. Pulsa Reanudar para continuar. | Publicidade da Twitch · em pausa. Pressione Retomar para continuar. | Publicité Twitch · en pause. Appuyez sur Reprendre pour continuer.
Twitch ad break · about {seconds} s remaining | Publicidad de Twitch · quedan unos {seconds} s | Publicidade da Twitch · faltam cerca de {seconds} s | Publicité Twitch · environ {seconds} s restantes
Twitch ad break · waiting for the stream to return… | Publicidad de Twitch · esperando que vuelva la emisión… | Publicidade da Twitch · aguardando o retorno da transmissão… | Publicité Twitch · en attente du retour du direct…
Twitch did not resume after the ad break. Press Restart to try again. | Twitch no reanudó la emisión después de la publicidad. Pulsa Reiniciar para reintentar. | A Twitch não retomou a transmissão após a publicidade. Pressione Reiniciar para tentar novamente. | Twitch n’a pas repris le direct après la publicité. Appuyez sur Redémarrer pour réessayer.
Video resolution | Resolución del video | Resolução do vídeo | Résolution de la vidéo
Highest available | Máxima disponible | Máxima disponível | Meilleure disponible
Highest | Máxima | Máxima | Maximale
Quality | Calidad | Qualidade | Qualité
Paste a link to check its resolutions. | Pega un enlace para consultar sus resoluciones. | Cole um link para consultar as resoluções. | Collez un lien pour consulter les résolutions.
Checking resolutions… | Consultando resoluciones… | Consultando resoluções… | Recherche des résolutions…
Changing quality refills the delay buffer. X and Kick keep the available position; Twitch returns to live. Paused playback resumes. | Al cambiar se vuelve a llenar el retraso. X y Kick conservan el punto disponible; Twitch vuelve al directo. Si está en pausa, se reanuda. | Alterar a qualidade preenche novamente o buffer de atraso. X e Kick mantêm a posição disponível; a Twitch volta ao vivo. A reprodução pausada é retomada. | Changer la qualité remplit à nouveau le tampon. X et Kick conservent la position disponible ; Twitch revient au direct. La lecture en pause reprend.
{quality} · unavailable | {quality} · no disponible | {quality} · indisponível | {quality} · indisponible
Current quality: {quality} | Calidad actual: {quality} | Qualidade atual: {quality} | Qualité actuelle : {quality}
Changing quality prepares the delay again. | El cambio vuelve a preparar el retraso. | A alteração prepara o atraso novamente. | Le changement prépare à nouveau le délai.
Resolutions will be checked when playback starts. | Las resoluciones se consultarán al iniciar. | As resoluções serão consultadas ao iniciar. | Les résolutions seront recherchées au démarrage.
Switching to {quality}… | Cambiando a {quality}… | Alterando para {quality}… | Passage à {quality}…
the highest available | la máxima disponible | a máxima disponível | la meilleure disponible
Stream position | Momento de la emisión | Posição da transmissão | Position dans le direct
Checking history… | Consultando historial… | Consultando histórico… | Recherche de l’historique…
LIVE | EN VIVO | AO VIVO | EN DIRECT
Return to live with the configured delay | Volver al directo con el retraso configurado | Voltar ao vivo com o atraso configurado | Revenir au direct avec le délai configuré
Return to live and refill the buffer | Volver al directo y preparar de nuevo el buffer | Voltar ao vivo e preencher o buffer novamente | Revenir au direct et remplir à nouveau le tampon
{duration} available | {duration} disponibles | {duration} disponíveis | {duration} disponibles
{duration} behind the available edge | A {duration} del extremo disponible | A {duration} do limite disponível | À {duration} du point disponible le plus récent
Windows default | Predeterminada de Windows | Padrão do Windows | Sortie Windows par défaut
Langbuffer | Langbuffer | Langbuffer | Langbuffer
Langbuffer — settings | Langbuffer — configuración | Langbuffer — configurações | Langbuffer — paramètres
Negative moves subtitles earlier; positive moves them later. 0.0 s keeps the original timing. Changes apply to upcoming pages; the current page stays fixed. | Negativo adelanta; positivo retrasa. 0,0 s conserva los tiempos originales. Se aplica a las próximas páginas; la que ya se muestra permanece fija. | Valores negativos adiantam as legendas; positivos as atrasam. 0,0 s mantém o tempo original. As alterações valem para as próximas páginas; a atual permanece fixa. | Une valeur négative avance les sous-titres ; une valeur positive les retarde. 0,0 s conserve le timing original. Les changements concernent les prochaines pages ; la page actuelle reste fixe.
Langbuffer supports X broadcasts and Twitch/Kick channels. | Langbuffer admite broadcasts de X y canales de Twitch/Kick. | Langbuffer aceita transmissões do X e canais da Twitch/Kick. | Langbuffer prend en charge les diffusions X et les chaînes Twitch/Kick.
Download the selected language packages before starting. | Descarga los paquetes de los idiomas elegidos antes de Iniciar. | Baixe os pacotes dos idiomas escolhidos antes de iniciar. | Téléchargez les packs des langues choisies avant de démarrer.
Select an available audio output. | Selecciona una salida de escucha disponible. | Selecione uma saída de áudio disponível. | Sélectionnez une sortie audio disponible.
Restarting the connection… | Reiniciando la conexión… | Reiniciando a conexão… | Redémarrage de la connexion…
Local settings could not be saved. | No se pudieron guardar los ajustes locales. | Não foi possível salvar as configurações locais. | Impossible d’enregistrer les paramètres locaux.
Preferences could not be read; defaults will be used. | No se pudieron leer las preferencias; se usarán los valores iniciales. | Não foi possível ler as preferências; serão usados os valores padrão. | Impossible de lire les préférences ; les valeurs par défaut seront utilisées.
Audio devices could not be listed. Restart the app and try again. | No se pudieron enumerar los dispositivos de audio. Reinicia la app y vuelve a intentarlo. | Não foi possível listar os dispositivos de áudio. Reinicie o aplicativo e tente novamente. | Impossible de lister les périphériques audio. Redémarrez l’application et réessayez.
Paste a Kick/Twitch stream or an X broadcast/video post. | Pega un directo de Kick/Twitch o una retransmisión/publicación con video de X. | Cole uma transmissão do Kick/Twitch ou uma transmissão/publicação com vídeo do X. | Collez un direct Kick/Twitch ou une diffusion/publication vidéo X.
Paste an X broadcast or video post link. | Pega un enlace de retransmisión o de una publicación con video de X. | Cole um link de transmissão ou publicação com vídeo do X. | Collez un lien de diffusion ou de publication vidéo X.
Paste a https://x.com/i/broadcasts/… link | Pega un enlace https://x.com/i/broadcasts/… | Cole um link https://x.com/i/broadcasts/… | Collez un lien https://x.com/i/broadcasts/…
Twitch changed format repeatedly and exhausted automatic retries. Wait a moment and press Restart. | Twitch cambió de formato repetidamente y agotó los reintentos automáticos. Espera un momento y pulsa Reiniciar. | A Twitch mudou de formato repetidamente e esgotou as tentativas automáticas. Aguarde um momento e pressione Reiniciar. | Twitch a changé de format à plusieurs reprises et épuisé les tentatives automatiques. Patientez un instant et appuyez sur Redémarrer.
The stream removed pending content. Press Restart to return near live. | El directo retiró contenido pendiente. Pulsa Reiniciar para volver cerca del directo. | A transmissão removeu conteúdo pendente. Pressione Reiniciar para voltar perto do ao vivo. | Le direct a retiré du contenu en attente. Appuyez sur Redémarrer pour revenir près du direct.
The server stopped publishing segments. You can press Restart. | El servidor dejó de publicar segmentos. Puedes pulsar Reiniciar. | O servidor parou de publicar segmentos. Você pode pressionar Reiniciar. | Le serveur a cessé de publier des segments. Vous pouvez appuyer sur Redémarrer.
That Twitch channel is not live. | Ese canal de Twitch no está en directo. | Esse canal da Twitch não está ao vivo. | Cette chaîne Twitch n’est pas en direct.
The Twitch stream could not be accessed. | No se pudo acceder al directo de Twitch. | Não foi possível acessar a transmissão da Twitch. | Impossible d’accéder au direct Twitch.
That Kick channel is not live. | Ese canal de Kick no está en directo. | Esse canal do Kick não está ao vivo. | Cette chaîne Kick n’est pas en direct.
The Kick stream could not be accessed. | No se pudo acceder al directo de Kick. | Não foi possível acessar a transmissão do Kick. | Impossible d’accéder au direct Kick.
Kick has not published an active replay for seeking. | Kick no publicó una repetición activa para poder retroceder. | O Kick não publicou uma reprise ativa para permitir voltar. | Kick n’a pas publié de replay actif permettant de revenir en arrière.
That resolution is no longer available. Choose another. | Esa resolución ya no está disponible. Elige otra. | Essa resolução não está mais disponível. Escolha outra. | Cette résolution n’est plus disponible. Choisissez-en une autre.
That resolution and FPS combination is no longer available. Choose another. | Esa combinación de resolución y FPS ya no está disponible. Elige otra. | Essa combinação de resolução e FPS não está mais disponível. Escolha outra. | Cette combinaison de résolution et d’images par seconde n’est plus disponible. Choisissez-en une autre.
A video segment exceeds the temporary buffer limit. Choose another quality. | Un segmento del video supera el límite temporal de seguridad. Elige otra calidad. | Um segmento de vídeo excede o limite do buffer temporário. Escolha outra qualidade. | Un segment vidéo dépasse la limite du tampon temporaire. Choisissez une autre qualité.
Error: {message} | Error: {message} | Erro: {message} | Erreur : {message}
Playback failed ({code}). | La reproducción falló ({code}). | A reprodução falhou ({code}). | La lecture a échoué ({code}).
Speech recognition or translation failed ({code}). | El reconocimiento o la traducción falló ({code}). | O reconhecimento ou a tradução falhou ({code}). | La reconnaissance ou la traduction a échoué ({code}).
The stream could not be played ({code}). | No se pudo reproducir el directo ({code}). | Não foi possível reproduzir a transmissão ({code}). | Impossible de lire le direct ({code}).
Session cleanup failed ({code}). | La limpieza de la sesión falló ({code}). | A limpeza da sessão falhou ({code}). | Le nettoyage de la session a échoué ({code}).
This is how subtitles will look in the preview.\nUse this sample to check opacity and size. | Así se verán los subtítulos en el ejemplo,\ncon esto puedes comprobar la opacidad y el tamaño, etc. | As legendas aparecerão assim na prévia.\nUse este exemplo para conferir a opacidade e o tamanho. | Voici l’apparence des sous-titres dans l’aperçu.\nUtilisez cet exemple pour vérifier l’opacité et la taille.
Local · English · {model} | Local · inglés · {model} | Local · inglês · {model} | Local · anglais · {model}
Recognition {model} | Reconocimiento {model} | Reconhecimento {model} | Reconnaissance {model}
Recognition {model} · multilingual | Reconocimiento {model} · multilingüe | Reconhecimento {model} · multilíngue | Reconnaissance {model} · multilingue
Translation {source} → {target} | Traducción {source} → {target} | Tradução {source} → {target} | Traduction {source} → {target}
Choose the model before starting. Both recognize English locally. | Elige el modelo antes de Iniciar. Ambos reconocen inglés localmente. | Escolha o modelo antes de iniciar. Ambos reconhecem inglês localmente. | Choisissez le modèle avant de démarrer. Les deux reconnaissent l’anglais localement.
Choose the input and output languages, then download the packages you need. | Elige los idiomas de entrada y salida y descarga los paquetes que necesites. | Escolha os idiomas de entrada e saída e baixe os pacotes necessários. | Choisissez les langues d’entrée et de sortie, puis téléchargez les packs nécessaires.
Cancel | Cancelar | Cancelar | Annuler
Available packages | Paquetes disponibles | Pacotes disponíveis | Packs disponibles
Download selected | Descargar seleccionado | Baixar selecionado | Télécharger la sélection
Done | Listo | Concluído | Terminé
Recognition packages are shared by input languages. Translation packages are directional; some pairs use English as a bridge. | Los idiomas de entrada comparten paquetes de reconocimiento. Los paquetes de traducción tienen una dirección; algunos pares usan inglés como puente. | Os idiomas de entrada compartilham pacotes de reconhecimento. Os pacotes de tradução têm uma direção; alguns pares usam inglês como ponte. | Les langues d’entrée partagent les packs de reconnaissance. Les packs de traduction sont directionnels ; certaines paires utilisent l’anglais comme intermédiaire.
Local recognition model for the input language. | Modelo de reconocimiento local para el idioma de entrada. | Modelo de reconhecimento local para o idioma de entrada. | Modèle de reconnaissance local pour la langue d’entrée.
Local · multilingual · small | Local · multilingüe · small | Local · multilíngue · small | Local · multilingue · small
One small package recognizes all non-English input languages in this list. | Un solo paquete small reconoce todos los idiomas de entrada no ingleses de esta lista. | Um único pacote small reconhece todos os idiomas de entrada desta lista, exceto inglês. | Un seul pack small reconnaît toutes les langues d’entrée de cette liste, sauf l’anglais.
Open language manager | Abrir el gestor de idiomas | Abrir o gerenciador de idiomas | Ouvrir le gestionnaire de langues
Language package progress | Progreso de los paquetes de idiomas | Progresso dos pacotes de idiomas | Progression des packs de langues
Available language packages | Paquetes de idiomas disponibles | Pacotes de idiomas disponíveis | Packs de langues disponibles
Package | Paquete | Pacote | Pack
Status | Estado | Estado | État
Download | Descarga | Download | Téléchargement
Wait until package preparation finishes. | Espera a que termine la preparación de los paquetes. | Aguarde o fim da preparação dos pacotes. | Attendez la fin de la préparation des packs.
Input: {source}\nOutput: {target} | Entrada: {source}\nSalida: {target} | Entrada: {source}\nSaída: {target} | Entrée : {source}\nSortie : {target}
Local translation: {source} → English → {target}. English is used as a bridge. | Traducción local: {source} → Inglés → {target}. Se usa inglés como puente. | Tradução local: {source} → Inglês → {target}. O inglês é usado como ponte. | Traduction locale : {source} → Anglais → {target}. L’anglais sert d’intermédiaire.
Captions use the input language; no translation package is needed. | Los subtítulos usan el idioma de entrada; no hace falta un paquete de traducción. | As legendas usam o idioma de entrada; não é necessário um pacote de tradução. | Les sous-titres utilisent la langue d’entrée ; aucun pack de traduction n’est nécessaire.
Local translation: {source} → {target}. | Traducción local: {source} → {target}. | Tradução local: {source} → {target}. | Traduction locale : {source} → {target}.
Removing package… | Borrando paquete… | Removendo pacote… | Suppression du pack…
Preparing packages… | Preparando paquetes… | Preparando pacotes… | Préparation des packs…
Download required packages · {size} | Descargar paquetes necesarios · {size} | Baixar pacotes necessários · {size} | Télécharger les packs nécessaires · {size}
Required packages installed | Paquetes necesarios instalados | Pacotes necessários instalados | Packs nécessaires installés
Packages are ready for the selected languages. | Los paquetes están preparados para los idiomas elegidos. | Os pacotes estão prontos para os idiomas escolhidos. | Les packs sont prêts pour les langues choisies.
Required: {packages}. | Necesarios: {packages}. | Necessários: {packages}. | Nécessaires : {packages}.
Included · read-only | Incluido · solo lectura | Incluído · somente leitura | Inclus · lecture seule
Included · incomplete | Incluido · incompleto | Incluído · incompleto | Inclus · incomplet
Installed | Instalado | Instalado | Installé
Incomplete | Incompleto | Incompleto | Incomplet
Not installed | No instalado | Não instalado | Non installé
Confirm removal | Confirmar borrado | Confirmar remoção | Confirmer la suppression
Remove selected | Borrar seleccionado | Remover selecionado | Supprimer la sélection
Select a package to download or remove it. | Selecciona un paquete para descargarlo o borrarlo. | Selecione um pacote para baixar ou remover. | Sélectionnez un pack à télécharger ou à supprimer.
This package is included with Langbuffer and cannot be removed here. | Este paquete viene incluido con Langbuffer y no se puede borrar aquí. | Este pacote está incluído no Langbuffer e não pode ser removido aqui. | Ce pack est inclus dans Langbuffer et ne peut pas être supprimé ici.
Remove {package}? Languages that need it will require another download. | ¿Borrar {package}? Los idiomas que lo necesiten requerirán otra descarga. | Remover {package}? Os idiomas que precisam dele exigirão outro download. | Supprimer {package} ? Les langues qui en ont besoin nécessiteront un nouveau téléchargement.
This recognition package is shared by all non-English input languages. | Todos los idiomas de entrada salvo inglés comparten este paquete de reconocimiento. | Todos os idiomas de entrada, exceto inglês, compartilham este pacote de reconhecimento. | Toutes les langues d’entrée, sauf l’anglais, partagent ce pack de reconnaissance.
This package can be downloaded independently of the current language selection. | Este paquete puede descargarse independientemente de los idiomas elegidos. | Este pacote pode ser baixado independentemente dos idiomas escolhidos. | Ce pack peut être téléchargé indépendamment des langues choisies.
Preparing download… | Preparando la descarga… | Preparando o download… | Préparation du téléchargement…
Downloading | Descargando | Baixando | Téléchargement
Verifying | Verificando | Verificando | Vérification
Preparing | Preparando | Preparando | Préparation
Ready | Listo | Pronto | Prêt
language packages | paquetes de idiomas | pacotes de idiomas | packs de langues
{action} {package} · {done} / {total} | {action} {package} · {done} / {total} | {action} {package} · {done} / {total} | {action} {package} · {done} / {total}
{action} {package} | {action} {package} | {action} {package} | {action} {package}
Cancelling package preparation… | Cancelando la preparación de los paquetes… | Cancelando a preparação dos pacotes… | Annulation de la préparation des packs…
The package could not be removed. Close any session using it and try again. | No se pudo borrar el paquete. Cierra las sesiones que lo estén usando y vuelve a intentarlo. | Não foi possível remover o pacote. Feche as sessões que o estejam usando e tente novamente. | Impossible de supprimer le pack. Fermez les sessions qui l’utilisent et réessayez.
Packages could not be prepared. Check your connection and free space, then try again. | No se pudieron preparar los paquetes. Comprueba la conexión y el espacio libre y vuelve a intentarlo. | Não foi possível preparar os pacotes. Verifique a conexão e o espaço livre e tente novamente. | Impossible de préparer les packs. Vérifiez la connexion et l’espace libre, puis réessayez.
Stop playback in every window before changing shared packages. | Detén la reproducción en todas las ventanas antes de cambiar los paquetes compartidos. | Pare a reprodução em todas as janelas antes de alterar os pacotes compartilhados. | Arrêtez la lecture dans toutes les fenêtres avant de modifier les packs partagés.
Another window is preparing packages. Try again when it finishes. | Otra ventana está preparando paquetes. Inténtalo cuando termine. | Outra janela está preparando pacotes. Tente novamente quando terminar. | Une autre fenêtre prépare des packs. Réessayez lorsqu’elle aura terminé.
Preparation cancelled. Packages that finished installing are kept. | Preparación cancelada. Se conservan los paquetes que terminaron de instalarse. | Preparação cancelada. Os pacotes cuja instalação terminou são mantidos. | Préparation annulée. Les packs dont l’installation est terminée sont conservés.
Package removed. Download it again before using languages that need it. | Paquete borrado. Descárgalo de nuevo antes de usar idiomas que lo necesiten. | Pacote removido. Baixe-o novamente antes de usar os idiomas que precisam dele. | Pack supprimé. Téléchargez-le à nouveau avant d’utiliser les langues qui en ont besoin.
Download complete. | Descarga completada. | Download concluído. | Téléchargement terminé.
Use | Usar | Usar | Utiliser
In use | En uso | Em uso | Utilisé
Remove | Borrar | Remover | Supprimer
No translation package needed | No hace falta un paquete de traducción | Nenhum pacote de tradução é necessário | Aucun pack de traduction nécessaire
Input packages translate each language into English. | Los paquetes de entrada traducen cada idioma al inglés. | Os pacotes de entrada traduzem cada idioma para o inglês. | Les packs d’entrée traduisent chaque langue vers l’anglais.
Output packages translate English into each language. | Los paquetes de salida traducen del inglés a cada idioma. | Os pacotes de saída traduzem do inglês para cada idioma. | Les packs de sortie traduisent l’anglais vers chaque langue.
Recognition converts audio into text and is managed separately. | El reconocimiento convierte el audio en texto y se gestiona por separado. | O reconhecimento converte áudio em texto e é gerenciado separadamente. | La reconnaissance convertit l’audio en texte et se gère séparément.
For English input | Para entrada en inglés | Para entrada em inglês | Pour une entrée en anglais
For non-English input | Para entrada en otros idiomas | Para entrada em outros idiomas | Pour une entrée dans une autre langue
Used automatically for this input language | Se usa automáticamente para este idioma de entrada | Usado automaticamente para este idioma de entrada | Utilisé automatiquement pour cette langue d’entrée
Select an English input language to use this model. | Selecciona inglés como idioma de entrada para usar este modelo. | Selecione inglês como idioma de entrada para usar este modelo. | Sélectionnez l’anglais comme langue d’entrée pour utiliser ce modèle.
Select a non-English input language to use this model. | Selecciona un idioma de entrada distinto del inglés para usar este modelo. | Selecione um idioma de entrada diferente do inglês para usar este modelo. | Sélectionnez une langue d’entrée autre que l’anglais pour utiliser ce modèle.
{source} → {target} | {source} → {target} | {source} → {target} | {source} → {target}
Active configuration | Configuración activa | Configuração ativa | Configuration active
Input: {source} · Output: {target} | Entrada: {source} · Salida: {target} | Entrada: {source} · Saída: {target} | Entrée : {source} · Sortie : {target}
Recognition: {model} | Reconocimiento: {model} | Reconhecimento: {model} | Reconnaissance : {model}
Additional installed package | Paquete adicional instalado | Pacote adicional instalado | Pack supplémentaire installé
Included with Langbuffer | Incluido con Langbuffer | Incluído no Langbuffer | Inclus dans Langbuffer
Manage and select the languages used by the app. | Gestiona y selecciona los idiomas que usa la app. | Gerencie e selecione os idiomas usados pelo aplicativo. | Gérez et sélectionnez les langues utilisées par l’application.
Ready to start | Listo para iniciar | Pronto para iniciar | Prêt à démarrer
Required packages are missing | Faltan paquetes necesarios | Faltam pacotes necessários | Des packs nécessaires sont manquants
Remove this package? Click again to confirm. | ¿Borrar este paquete? Pulsa de nuevo para confirmar. | Remover este pacote? Clique novamente para confirmar. | Supprimer ce pack ? Cliquez à nouveau pour confirmer.
Download package | Descargar paquete | Baixar pacote | Télécharger le pack
Package size: {size} | Tamaño del paquete: {size} | Tamanho do pacote: {size} | Taille du pack : {size}
'''


def _catalog(rows):
    result = {}
    for row in rows.strip().splitlines():
        values = [value.replace('\\n', '\n') for value in row.split(' | ')]
        if len(values) != 4:
            raise ValueError('Invalid translation catalog row: ' + row)
        result[values[0]] = dict(zip(SUPPORTED_LOCALES, values))
    return result


CATALOG = _catalog(_ROWS)
PREVIEW_KEY = 'This is how subtitles will look in the preview.\nUse this sample to check opacity and size.'


class LocalizedText(str):
    def __new__(cls, value, key, values):
        instance = super().__new__(cls, value)
        instance.key, instance.values = key, values
        return instance


class UiLocalization:
    """Translate an inherited UI instance and remember canonical property values."""

    def __init__(self, window, locale='en'):
        self.window = window
        self.locale = locale if locale in SUPPORTED_LOCALES else 'en'
        self._registered = set()
        self._bindings = {}
        self._combos = {}
        self._aliases = {}
        self._patterns = []
        for key, variants in CATALOG.items():
            for source in variants.values():
                fields = list(string.Formatter().parse(source))
                if any(field is not None for _, field, _, _ in fields):
                    if not any(literal.strip() for literal, _, _, _ in fields):
                        # Progress uses explicit LocalizedText keys. A template
                        # consisting only of placeholders must not match every
                        # unrelated phrase or a device name.
                        continue
                    pattern = ''.join(re.escape(literal) + (f'(?P<{field}>.*?)' if field else '')
                                      for literal, field, _, _ in fields)
                    self._patterns.append((re.compile('^'+pattern+'$', re.DOTALL), key))
                else:
                    self._aliases[source] = key
                    if key in ('English', 'Spanish', 'Portuguese', 'French', 'German', 'Italian',
                               'Japanese', 'Korean', 'Simplified Chinese'):
                        self._aliases[source.lower()] = key
        # Rank literal text, not regex/group-name length. Otherwise the broad
        # "{action}: {name}" can consume "Error: {message}" and leave its prefix
        # untranslated even though a specific localized template exists.
        self._patterns.sort(key=lambda item: -sum(len(literal) for literal, _, _, _
                                                 in string.Formatter().parse(item[1])))
        self.register_tree(window)
        self._install_preview()

    def _source(self, value):
        if isinstance(value, LocalizedText):
            return value.key, dict(value.values)
        value = str(value)
        if value in self._aliases:
            return self._aliases[value], {}
        for pattern, key in self._patterns:
            found = pattern.fullmatch(value)
            if found:
                return key, found.groupdict()
        return value, {}

    def text(self, key, **values):
        if not values:
            key, values = self._source(key)
        template = CATALOG.get(key, {}).get(self.locale, key)
        rendered = {name: self._translate(str(value)) if name not in ('code', 'model') else str(value)
                    for name, value in values.items()}
        return LocalizedText(template.format(**rendered) if values else template, key, values)

    def _translate(self, value):
        if value == 'source_twitch_ad_timeout':
            return self.text('Twitch did not resume after the ad break. Press Restart to try again.')
        key, values = self._source(value)
        if key in CATALOG:
            return self.text(key, **values)
        if '\n' in value:
            return '\n'.join(str(self._translate(line)) for line in value.splitlines())
        if re.fullmatch(r'[a-z][a-z0-9]*(?:_[A-Za-z0-9]+)+', value):
            if value.startswith('local_worker_') or value == 'worker_exited':
                key = 'Speech recognition or translation failed ({code}).'
            elif value.startswith('source_'):
                key = 'The stream could not be played ({code}).'
            elif 'cleanup' in value:
                key = 'Session cleanup failed ({code}).'
            else:
                key = 'Playback failed ({code}).'
            return self.text(key, code=value)
        return value

    def _skip(self, obj):
        overlay = getattr(self.window, 'caption_overlay', None)
        parent = obj
        while parent is not None:
            if parent is overlay:
                return True
            parent = parent.parent()
        return False

    def _bind(self, obj, getter, setter):
        identifier = (id(obj), setter)
        if identifier in self._bindings:
            return
        original = getattr(obj, setter)
        record = [obj, original, self._source(getattr(obj, getter)())]
        self._bindings[identifier] = record

        def update(value):
            record[2] = self._source(value)
            return original(self._translate(value))

        setattr(obj, setter, update)
        update(getattr(obj, getter)())
        obj.destroyed.connect(lambda *_: self._bindings.pop(identifier, None))

    def _register_combo(self, combo):
        identifier = id(combo)
        if identifier in self._combos:
            return
        originals = {name: getattr(combo, name) for name in ('addItem', 'addItems', 'insertItem',
                      'setItemText', 'clear', 'removeItem')}
        sources = []
        # An audio device description is data, even when it happens to match a
        # translated interface word. Only the synthetic default item is UI.
        device_combo = combo is getattr(getattr(self.window, 'panel', None), 'listener', None)

        def source(value, index):
            return (str(value), {}) if device_combo and index > 0 else self._source(value)

        def translated(value, index):
            return str(value) if device_combo and index > 0 else str(self._translate(value))

        for index in range(combo.count()):
            value = combo.itemText(index)
            sources.append(source(value, index))
            originals['setItemText'](index, translated(value, index))

        def add_item(*args, **kwargs):
            args = list(args)
            offset = 0 if isinstance(args[0], str) else 1
            index = combo.count()
            sources.append(source(args[offset], index))
            args[offset] = translated(args[offset], index)
            return originals['addItem'](*args, **kwargs)

        def add_items(values):
            for value in values:
                add_item(value)

        def insert_item(index, *args, **kwargs):
            args = list(args)
            offset = 0 if isinstance(args[0], str) else 1
            index = max(0, min(index, combo.count()))
            sources.insert(index, source(args[offset], index))
            args[offset] = translated(args[offset], index)
            return originals['insertItem'](index, *args, **kwargs)

        def set_item_text(index, value):
            if 0 <= index < len(sources):
                sources[index] = source(value, index)
            return originals['setItemText'](index, translated(value, index))

        def clear():
            sources.clear()
            return originals['clear']()

        def remove_item(index):
            if 0 <= index < len(sources):
                sources.pop(index)
            return originals['removeItem'](index)

        for name, function in (('addItem', add_item), ('addItems', add_items),
                               ('insertItem', insert_item), ('setItemText', set_item_text),
                               ('clear', clear), ('removeItem', remove_item)):
            setattr(combo, name, function)
        self._combos[identifier] = (combo, sources, originals['setItemText'], device_combo)
        combo.destroyed.connect(lambda *_: self._combos.pop(identifier, None))

    def register_tree(self, widget):
        """Register newly created dialogs/menus; safe to call more than once."""
        for obj in [widget, *widget.findChildren(QObject)]:
            if id(obj) in self._registered or self._skip(obj):
                continue
            self._registered.add(id(obj))
            obj.destroyed.connect(lambda *_, identifier=id(obj): self._registered.discard(identifier))
            if isinstance(obj, (QWidget, QAction)):
                for getter, setter in (('toolTip', 'setToolTip'), ('statusTip', 'setStatusTip'),
                                       ('whatsThis', 'setWhatsThis')):
                    self._bind(obj, getter, setter)
            if isinstance(obj, QWidget):
                for getter, setter in (('accessibleName', 'setAccessibleName'),
                                       ('accessibleDescription', 'setAccessibleDescription'),
                                       ('windowTitle', 'setWindowTitle')):
                    self._bind(obj, getter, setter)
            menu_action = (isinstance(obj, QAction) and isinstance(obj.parent(), QMenu)
                           and obj.parent().menuAction() is obj)
            if isinstance(obj, (QLabel, QAbstractButton, QAction)) and not menu_action:
                self._bind(obj, 'text', 'setText')
            if isinstance(obj, QLineEdit):
                self._bind(obj, 'placeholderText', 'setPlaceholderText')
            if isinstance(obj, QComboBox):
                self._register_combo(obj)
            if isinstance(obj, QMenu):
                self._bind(obj, 'title', 'setTitle')
                original_add = obj.addAction

                def add_action(*args, _original=original_add, **kwargs):
                    action = _original(*args, **kwargs)
                    if action is not None:
                        self.register_tree(action)
                    return action

                obj.addAction = add_action

    def set_language(self, output_code):
        self.locale = output_code if output_code in SUPPORTED_LOCALES else 'en'
        self.register_tree(self.window)
        for obj, setter, (key, values) in list(self._bindings.values()):
            if isValid(obj):
                setter(self.text(key, **values) if key in CATALOG else self._translate(key))
        for combo, sources, setter, device_combo in list(self._combos.values()):
            if isValid(combo):
                blocked = combo.blockSignals(True)
                try:
                    for index, (key, values) in enumerate(sources[:combo.count()]):
                        value = key if device_combo and index > 0 else self.text(key, **values)
                        setter(index, value)
                finally:
                    combo.blockSignals(blocked)
        overlay = getattr(self.window, 'caption_overlay', None)
        if overlay is not None:
            self._preview_globals['PREVIEW_TEXT'] = str(self.text(PREVIEW_KEY))
            overlay.update()
        if hasattr(self.window, 'layout_wait_label'):
            self.window.layout_wait_label()

    def _install_preview(self):
        overlay = getattr(self.window, 'caption_overlay', None)
        if overlay is None:
            return
        # Reuse the stable painter's bytecode with a private globals dictionary.
        # This preserves its layout while changing neither module state nor
        # stored captions, and lets multiple windows use different UI languages.
        original = overlay.paintEvent.__func__
        self._preview_globals = dict(original.__globals__, PREVIEW_TEXT=str(self.text(PREVIEW_KEY)))
        paint = FunctionType(original.__code__, self._preview_globals,
                             original.__name__, original.__defaults__, original.__closure__)
        overlay.paintEvent = MethodType(paint, overlay)
