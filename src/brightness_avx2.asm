; rcx = a, rdx = b, r8 = count. Result in rax
; One instruction compares 32 pixels using vpsadbw
; Any pixels left over are added one at a time

bits 64
default rel

section .text
global brightness_avx2

brightness_avx2:
    ; clear registers
    vpxor ymm0, ymm0, ymm0
    xor r10, r10 ; will be the counter of how many pixels analyzed

    ; pixel count
    mov r9, r8

    ; to round down to a multiple of 32 in case of spare pixels
    ; therefore r9 is the pixel count without the excess pixels
    and r9, -32
    
    test r9, r9 ; returns zero if less than 32 remain
    jz .combine

.loop:
    vmovdqu ymm1, [rcx + r10] ; 32 pixels from the first frame
    vmovdqu ymm2, [rdx + r10] ; the same 32 from the second frame
    vpsadbw ymm1, ymm1, ymm2 ; absolute change for each byte batch
    vpaddq  ymm0, ymm0, ymm1 ; add the four groups into the running total
    add r10, 32 ; move forward 32 pixels
    
    cmp r10, r9 ; reached the rounded total pixel count?
    jb .loop

.combine:
    vextracti128 xmm1, ymm0, 1 ; top two groups of the absolute difference
    vpaddq xmm0, xmm0, xmm1    ; add them into the bottom two
    vmovq rax, xmm0 ; low group
    vpextrq r11, xmm0, 1 ; high group
    add rax, r11 ; one total
    vzeroupper ; clear the wide registers before returning

.tail:
    cmp r10, r8 ; any pixels past the multiple of 32?
    jae .done

    ; Get the remaining absolute difference
    movzx r11d, byte [rcx + r10] ; one pixel from the first frame
    movzx r9d, byte [rdx + r10]  ; the matching pixel from the second frame
    sub r11d, r9d ; first minus second
    movsxd  r11, r11d ; keep the sign
    mov r9, r11       ; save the signed difference
    neg r11           ; flip its sign
    test r9, r9       ; was the original negative?
    cmovns r11, r9    ; if it was already positive, put it back
    add rax, r11 ; add this pixel's absolute change
    inc r10
    jmp .tail

.done:
    ret
