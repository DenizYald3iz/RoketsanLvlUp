# Case 1: Drone Görüntülerinde Araç Tespiti

Drone görüntülerinde `car`, `van`, `truck` ve `bus` sınıflarındaki araçların tespiti. Çözümün tamamı [`solution.ipynb`](solution.ipynb) içinde.

**Final skor: Public 0.80740 (mAP@0.5)**

## Yöntem
- **Dedektör:** RF-DETR-Large. Görüntüler 704 px'lik tile'lara bölünerek (%25 overlap) ve 1.33 zoom ile eğitildi, çünkü araçlar çok küçük (medyan kutu ~30 px).
- **Sahne bazlı train/val ayrımı:** Rastgele split'te aynı sahneden gelen kareler hem train'e hem val'e düşüyordu, bu yüzden val skoru olduğundan yüksek çıkıyordu. DINOv2 embedding benzerliğiyle benzer kareler gruplandı; her grup ya train'e ya val'e ayrıldı.
- **TTA:** 3 geçiş (zoom 1.33, zoom 1.33 + yatay çevirme, zoom 1.6), ardından sınıf bazlı NMS.
- **İkinci aşama sınıflandırıcı:** Hata analizinde en büyük sorun van→car karışıklığıydı. Bunu azaltmak için kutular ConvNeXt-Tiny ile yeniden skorlandı: `score^(1-α) · p[sınıf]^α`, α = 0.12.
- **Final model:** Tüm veriyle (val dahil) 7 epoch eğitildi, 6. epoch'un EMA ağırlıkları kullanıldı.

## Skorlar
| Adım | Val mAP@0.5 (sahne bazlı) | Public |
|---|---|---|
| İlk model: rastgele split, zoom yok, 12 epoch | 0.819 | 0.767 |
| Sahne bazlı split + zoom 1.33 + augmentation, 6 epoch | 0.8384 | 0.78883 |
| + 3 geçişli TTA | 0.8453 | 0.79966 |
| + ConvNeXt-T sınıflandırıcı (α = 0.12) | 0.8541 | 0.80358 |
| + tüm veriyle eğitim, 6. epoch EMA | – (val eğitimde) | **0.80740** |

Donanım: 1× NVIDIA RTX PRO 6000 (96 GB), Google Colab.
